"""
Обработчики создания заявок на покупку и откликов на заявки продажи
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from decimal import Decimal, InvalidOperation

from database import (
    get_user, create_deal, get_deal, update_deal_status,
    set_buy_amount, claim_deal_exclusive, send_system_message,
    is_valid_btc_bech32, calculate_seller_amounts, calculate_buyer_amounts,
    format_commission_info_buyer, send_notification, DealStatus,
    NUM_RE, db, is_user_registered, update_user_btc_wallet,
    send_public_request_notification
)
from keyboards import paid_or_cancel_kb
from config import ESCROW_BTC_ADDRESS, AMOUNT_PROMPT_TPL
from utils import format_currency_amount

router = Router()


# ==================== FSM СОСТОЯНИЯ ====================

class BuyStates(StatesGroup):
    WAIT_AMOUNT = State()


class BuyCreateStates(StatesGroup):
    WAIT_LIMITS_OR_AMOUNT = State()
    WAIT_WALLET = State()


class BuyMatchStates(StatesGroup):
    WAIT_SELLER_DETAILS = State()


# ==================== СОЗДАНИЕ ЗАЯВКИ НА ПОКУПКУ ====================

@router.callback_query(F.data == "buy:create")
async def buy_create_entry(callback: CallbackQuery, state: FSMContext):
    """Начало создания заявки на покупку"""
    u = await get_user(callback.from_user.id)
    success = (u["success_count"] if u else 0)
    
    if success < 100:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sell_or_buy:menu")]
        ])
        try:
            await callback.message.edit_text(
                "🚫 <b>Недостаточно опыта</b>\n\n"
                "❌ <b>Для создания заявки на покупку необходимо совершить 100 успешных сделок</b>\n\n"
                "💡 <b>Совет:</b> Сначала откликнитесь на заявки продажи, чтобы набрать опыт!",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                "🚫 <b>Недостаточно опыта</b>\n\n"
                "❌ <b>Для создания заявки на покупку необходимо совершить 100 успешных сделок</b>\n\n"
                "💡 <b>Совет:</b> Сначала откликнитесь на заявки продажи, чтобы набрать опыт!",
                reply_markup=kb,
                parse_mode="HTML"
            )
        await callback.answer()
        return
    
    await state.set_state(BuyCreateStates.WAIT_LIMITS_OR_AMOUNT)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="buy:cancel")]
    ])
    
    text = (
        "✨ <b>Создание заявки на покупку BTC</b> ✨\n\n"
        "📥 <b>Укажите сумму в BTC:</b>\n\n"
        "⚠️ <b>Важно:</b> Указывайте сумму только в BTC"
    )
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception:
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=kb
        )
    await callback.answer()


@router.message(BuyCreateStates.WAIT_LIMITS_OR_AMOUNT)
async def buy_create_limits_or_amount(message: Message, state: FSMContext):
    """Получение лимитов или суммы для покупки"""
    # Проверка на отмену
    if message.text and message.text.strip().lower() in ["отмена", "cancel", "❌ отмена"]:
        await state.clear()
        await message.answer(
            "❌ Создание заявки отменено",
        )
        return

    if not message.text:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="buy:create")]
        ])
        await message.answer(
            "❌ <b>Нужно отправить сумму текстом</b>",
            parse_mode="HTML",
            reply_markup=kb
        )
        return
    
    import logging
    logger = logging.getLogger(__name__)
    
    # Сохраняем исходный текст для логирования
    original_text = message.text.strip()
    logger.info(f"Обработка ввода суммы для покупки: '{original_text}'")
    
    # Заменяем запятую на точку (для поддержки европейского формата)
    # Убираем пробелы и неразрывные пробелы
    text_clean = original_text.replace(",", ".").replace(" ", "").replace("\u00A0", "").strip()
    nums = NUM_RE.findall(text_clean.lower())
    
    logger.info(f"Извлеченные числа: {nums}")
    
    if not nums:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="buy:create")]
        ])
        await message.answer(
            "❌ <b>Не распознал сумму</b>\n\n"
            "💡 <b>Правильные примеры:</b>\n"
            "• <code>0.005</code> — одна сумма в BTC\n"
            "• <code>0.001 - 0.10</code> — диапазон в BTC",
            parse_mode="HTML",
            reply_markup=kb
        )
        return
    
    try:
        if len(nums) == 1:
            # Одна сумма в BTC
            amount_str = nums[0]
            amount = Decimal(amount_str)
            min_btc = max_btc = amount
            logger.info(f"Одна сумма в BTC: {min_btc}")
        else:
            # Диапазон в BTC
            min_str = nums[0]
            max_str = nums[1]
            min_amount = Decimal(min_str)
            max_amount = Decimal(max_str)
            
            if max_amount < min_amount:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="buy:create")]
                ])
                await message.answer(
                    "❌ <b>Ошибка в диапазоне</b>\n\n"
                    "⚠️ <b>Максимум не может быть меньше минимума!</b>\n\n"
                    "💡 <b>Правильный пример:</b> <code>0.001 - 0.10</code>",
                    parse_mode="HTML",
                    reply_markup=kb
                )
                return
            
            min_btc = min_amount
            max_btc = max_amount
            logger.info(f"Диапазон в BTC: {min_btc} - {max_btc}")
    except InvalidOperation:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="buy:create")]
        ])
        await message.answer(
            "❌ <b>Некорректный формат</b>\n\n"
            "⚠️ <b>Используйте только числа и точку для десятичных дробей</b>\n\n"
            "💡 <b>Примеры:</b>\n"
            "• <code>0.005</code>\n"
            "• <code>0.001 - 0.10</code>",
            parse_mode="HTML",
            reply_markup=kb
        )
        return
    
    if min_btc <= 0 or max_btc <= 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="buy:create")]
        ])
        await message.answer(
            "❌ <b>Некорректная сумма</b>\n\n"
            "⚠️ <b>Значения должны быть больше нуля!</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return
    
    q = Decimal("0.00000001")
    min_btc = min_btc.quantize(q)
    max_btc = max_btc.quantize(q)
    
    # Показываем информацию о комиссии для покупателя
    seller_amounts = calculate_seller_amounts(max_btc)
    buyer_amounts = calculate_buyer_amounts(max_btc)
    
    # Формируем информацию о сумме
    if min_btc == max_btc:
        amount_info = f"💰 <b>Сумма:</b> {min_btc:.8f} BTC\n\n"
    else:
        amount_info = f"💰 <b>Диапазон:</b> {min_btc:.8f} - {max_btc:.8f} BTC\n\n"
    
    info = (
        f"{amount_info}"
        "💡 <b>Информация о комиссиях:</b>\n\n"
        f"✅ <b>Вы получите:</b> {buyer_amounts['buyer_receive']:.8f} BTC (100% от суммы покупки)\n"
        f"📊 <b>Комиссия платформы:</b> {buyer_amounts['buyer_commission']:.8f} BTC (0% - временно)\n"
        f"💵 <b>Вы заплатите:</b> {buyer_amounts['buyer_payment']:.8f} BTC\n"
        f"   (полная сумма, которую вы переводите продавцу)"
    )
    
    # Проверяем, есть ли BTC кошелек у пользователя
    user = await get_user(message.from_user.id)
    btc_wallet = user.get("btc_wallet") if user else None
    
    if btc_wallet:
        # У пользователя есть BTC кошелек, используем его автоматически
        await state.update_data(min_btc=str(min_btc), max_btc=str(max_btc))
        
        # Создаём заявку на покупку с BTC кошельком из профиля
        deal_id = await create_deal(
            message.from_user.id, min_btc, max_btc, ESCROW_BTC_ADDRESS,
            side="BUY", status=DealStatus.ACTIVE, buyer_wallet=btc_wallet
        )
        await send_public_request_notification(deal_id, bot=message.bot)
        
        await state.clear()
        
        # Рассчитываем сумму, которую получит продавец (полная сумма max_btc)
        from utils import get_btc_rub_rate, btc_to_rub
        btc_rub_rate = await get_btc_rub_rate()
        seller_receive_btc = max_btc  # Продавец получает полную сумму
        seller_receive_rub = btc_to_rub(seller_receive_btc, btc_rub_rate)
        seller_receive_rub_formatted = f"{seller_receive_rub:,.0f}".replace(",", " ")
        seller_receive_btc_formatted = f"{seller_receive_btc:.8f}".rstrip('0').rstrip('.')
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои сделки", callback_data="mydeals:active")],
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="menu:back")]
        ])
        await message.answer(
            "✨ <b>Заявка на покупку создана и активна!</b> ✨\n\n"
            f"💸 Вы получите: {seller_receive_rub_formatted} руб ({seller_receive_btc_formatted} BTC)\n\n"
            f"💼 <b>BTC кошелек:</b> <code>{btc_wallet}</code>\n\n"
            "✅ <b>Заявка доступна в разделе \"📉 Продать\"</b>\n\n"
            "💡 <b>Другие пользователи смогут откликнуться на вашу заявку</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        # BTC кошелек отсутствует, запрашиваем его
        await state.update_data(min_btc=str(min_btc), max_btc=str(max_btc))
        await state.set_state(BuyCreateStates.WAIT_WALLET)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="buy:cancel")]
        ])
        
        await message.answer(
            f"{info}\n\n"
            "⚠️ <b>Важно:</b> Вы будете платить на 2.5% больше в фиате\n"
            "💡 <b>Это компенсирует часть комиссии продавца</b>\n\n"
            "💼 <b>Укажите свой BTC кошелек:</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )


@router.callback_query(F.data == "buy:cancel")
async def buy_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена создания заявки на покупку"""
    await state.clear()
    
    # Возвращаем в меню "Продать или Купить"
    from keyboards import sell_or_buy_kb
    try:
        await callback.message.edit_text(
            "✨ <b>Создание заявки</b> ✨",
            reply_markup=sell_or_buy_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Создание заявки</b> ✨",
            reply_markup=sell_or_buy_kb(),
            parse_mode="HTML"
        )
    await callback.answer("Отменено")


@router.message(BuyCreateStates.WAIT_WALLET)
async def buy_create_wallet(message: Message, state: FSMContext):
    """Получение кошелька покупателя"""
    # Проверка на отмену
    if message.text and message.text.strip().lower() in ["отмена", "cancel", "❌ отмена"]:
        await state.clear()
        await message.answer(
            "❌ Создание заявки отменено",
        )
        return
    
    wallet = (message.text or "").strip()
    
    # Валидация BTC адреса
    if not is_valid_btc_bech32(wallet) and not (wallet.startswith("1") or wallet.startswith("3") or wallet.startswith("bc1")):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="buy:cancel")]
        ])
        await message.answer(
            "❌ <b>Неверный формат BTC адреса</b>\n\n"
            "💼 <b>Укажите ваш BTC кошелек:</b>\n\n"
            "📌 <b>Формат:</b> <code>bc1...</code> или <code>1...</code> или <code>3...</code>\n\n"
            "💡 <b>Пример:</b> <code>bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh</code>",
            parse_mode="HTML",
            reply_markup=kb
        )
        return
    
    data = await state.get_data()
    min_btc = Decimal(data["min_btc"])
    max_btc = Decimal(data["max_btc"])
    
    # Сохраняем BTC кошелек в профиль пользователя, если его там нет
    user = await get_user(message.from_user.id)
    if user and not user.get("btc_wallet"):
        await update_user_btc_wallet(message.from_user.id, wallet)
    
    # Создаём заявку на покупку
    deal_id = await create_deal(
        message.from_user.id, min_btc, max_btc, ESCROW_BTC_ADDRESS,
        side="BUY", status=DealStatus.ACTIVE, buyer_wallet=wallet
    )
    await send_public_request_notification(deal_id, bot=message.bot)
    
    await state.clear()
    
    # Рассчитываем сумму, которую получит продавец (полная сумма max_btc)
    from utils import get_btc_rub_rate, btc_to_rub
    btc_rub_rate = await get_btc_rub_rate()
    seller_receive_btc = max_btc  # Продавец получает полную сумму
    seller_receive_rub = btc_to_rub(seller_receive_btc, btc_rub_rate)
    seller_receive_rub_formatted = f"{seller_receive_rub:,.0f}".replace(",", " ")
    seller_receive_btc_formatted = f"{seller_receive_btc:.8f}".rstrip('0').rstrip('.')
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои сделки", callback_data="mydeals:active")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="menu:back")]
    ])
    await message.answer(
        "✨ <b>Заявка на покупку создана и активна!</b> ✨\n\n"
        f"💸 Вы получите: {seller_receive_rub_formatted} руб ({seller_receive_btc_formatted} BTC)\n\n"
        f"💼 <b>BTC кошелек:</b> <code>{wallet}</code>\n\n"
        "✅ <b>Заявка доступна в разделе \"📉 Продать\"</b>\n\n"
        "💡 <b>Другие пользователи смогут откликнуться на вашу заявку</b>",
        parse_mode="HTML",
        reply_markup=kb
    )


# ==================== ОТКЛИК НА ЗАЯВКУ (ПРОДАЖА/ПОКУПКА) ====================

@router.callback_query(F.data.startswith("buy:open:"))
async def buy_open(callback: CallbackQuery, state: FSMContext):
    """Отклик на заявку продажи (я покупаю)"""
    deal_id = int(callback.data.split(":")[2])
    d = await get_deal(deal_id)
    
    if not d or d["status"] != DealStatus.ACTIVE:
        await callback.answer("Заявка недоступна", show_alert=True)
        return
    
    if d["owner_id"] == callback.from_user.id:
        await callback.answer("Нельзя купить свою же заявку", show_alert=True)
        return

    await state.set_state(BuyStates.WAIT_AMOUNT)
    await state.update_data(
        deal_id=deal_id,
        min_btc=d["min_btc"],
        max_btc=d["max_btc"],
        seller_id=d["owner_id"]
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:view:{deal_id}")]
    ])
    await callback.message.answer(
        AMOUNT_PROMPT_TPL.format(min_btc=d["min_btc"], max_btc=d["max_btc"]),
        parse_mode="HTML",
        reply_markup=kb
    )
    await callback.answer()


@router.message(BuyStates.WAIT_AMOUNT)
async def buy_amount(message: Message, state: FSMContext):
    """Получение суммы покупки"""
    data = await state.get_data()
    deal_id = int(data["deal_id"])
    min_btc = Decimal(str(data["min_btc"]))
    max_btc = Decimal(str(data["max_btc"]))
    
    try:
        # Заменяем запятую на точку (для поддержки европейского формата)
        text_normalized = (message.text or "").replace(",", ".").replace(" ", "").replace("\u00A0", "").strip()
        nums = NUM_RE.findall(text_normalized)
        if not nums:
            raise ValueError()
        amt = Decimal(nums[0])
    except Exception:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:view:{deal_id}")]
        ])
        await message.answer(
            "❌ <b>Не удалось распознать сумму</b>\n\n"
            "💡 <b>Пример:</b> <code>0.005</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return
    
    q = Decimal("0.00000001")
    amt = amt.quantize(q)
    
    if not (min_btc <= amt <= max_btc):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:view:{deal_id}")]
        ])
        await message.answer(
            f"❌ <b>Сумма вне лимитов</b>\n\n"
            f"💡 <b>Допустимо от {min_btc} до {max_btc} BTC</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    # Рассчитываем что получит продавец (с учётом 5% комиссии)
    seller_amounts = calculate_seller_amounts(amt)
    
    # Рассчитываем что заплатит покупатель (+2.5% сверху)
    buyer_amounts = calculate_buyer_amounts(amt)
    
    # Сохраняем данные в сделку
    async with db() as conn:
        await conn.execute(
            "UPDATE deals SET buy_amount=?, buyer_extra=?, total_fiat_equivalent=?, "
            "net_to_seller=?, platform_commission=?, seller_net_loss=? WHERE id=?",
            (str(amt), 
             str(buyer_amounts["buyer_extra"]),
             str(buyer_amounts["total_fiat_equivalent"]),
             str(seller_amounts["net_to_seller"]),
             str(seller_amounts["platform_takes"]),
             str(seller_amounts["seller_net_loss"]), deal_id)
        )
        await conn.commit()
    
    await update_deal_status(deal_id, DealStatus.PAYMENT_PENDING)
    await state.clear()

    d = await get_deal(deal_id)
    details = d.get("payment_details") if d else None
    
    if not details:
        details = "Реквизиты временно недоступны. Свяжитесь с диспутологом."
    
    commission_info = format_commission_info_buyer(seller_amounts, buyer_amounts)
    
    text = (
        "✨ <b>Реквизиты продавца для оплаты</b> ✨\n\n"
        f"💳 <b>Реквизиты:</b>\n{details}\n\n"
        f"{commission_info}\n\n"
        "⚠️ <b>ВАЖНО: Переводите полную сумму!</b>\n\n"
        f"📊 <b>Детали платежа:</b>\n"
        f"💰 <b>Сумма к оплате:</b> {buyer_amounts['buyer_payment']:.8f} BTC\n"
        f"💵 <b>Вы получите:</b> {buyer_amounts['buyer_receive']:.8f} BTC (100% от суммы покупки)\n"
        f"📉 <b>Комиссия платформы:</b> {buyer_amounts['buyer_commission']:.8f} BTC (0% - временно)\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"<b>💰 ИТОГО К ПЕРЕВОДУ:</b> эквивалент {buyer_amounts['buyer_payment']:.8f} BTC в рублях/USDT"
    )
    
    await message.answer(text, parse_mode="HTML")
    # Получаем deal для проверки payment_marked_at
    d = await get_deal(deal_id)
    await message.answer(
        "💡 <b>После оплаты нажмите кнопку \"Оплатил\".</b>",
        reply_markup=paid_or_cancel_kb(deal_id, d),
        parse_mode="HTML"
    )

    # Уведомляем продавца
    if d:
        try:
            seller_info = (
                f"🔔 По вашей продаже #{deal_id} появился покупатель!\n\n"
                f"Базовая сумма: {amt} BTC\n"
                f"Комиссия платформы (-5%): {seller_amounts['platform_takes']} BTC\n"
                f"Покупатель компенсирует вам: эквивалент {seller_amounts['buyer_compensates']} BTC (+2.5%)\n"
                f"Ваша чистая комиссия: {seller_amounts['seller_net_loss']} BTC (2.5%)\n"
                f"Вы получите: эквивалент {seller_amounts['net_to_seller']} BTC в фиате"
            )
            await message.bot.send_message(d["owner_id"], seller_info)
        except Exception:
            pass


# ==================== ПРОДАВЕЦ УКАЗЫВАЕТ РЕКВИЗИТЫ (для BUY заявок) ====================

@router.message(BuyMatchStates.WAIT_SELLER_DETAILS)
async def seller_provide_details(message: Message, state: FSMContext):
    """Продавец указывает реквизиты для BUY заявки"""
    from database import set_deal_payment_details
    
    data = await state.get_data()
    deal_id = int(data.get("deal_id"))
    details = (message.text or "").strip()
    
    if len(details) < 5:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:view:{deal_id}")]
        ])
        await message.answer(
            "❌ Слишком короткие реквизиты. Укажите подробнее.",
            reply_markup=kb
        )
        return
    
    if len(details) > 500:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:view:{deal_id}")]
        ])
        await message.answer(
            "❌ Слишком длинные реквизиты (макс. 500 символов).",
            reply_markup=kb
        )
        return
    
    await set_deal_payment_details(deal_id, details)
    await state.clear()
    
    d = await get_deal(deal_id)
    if d:
        buyer_id = d["owner_id"]
        try:
            await message.bot.send_message(
                buyer_id,
                "💳 Реквизиты продавца для оплаты:\n" + details,
                reply_markup=paid_or_cancel_kb(deal_id, d)
            )
        except Exception:
            pass
    
    await message.answer("✅ Реквизиты сохранены. Ожидаем оплату покупателем.")