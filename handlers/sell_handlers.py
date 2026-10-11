"""
Обработчики создания заявок на продажу
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging

from database import (
    upsert_user, create_deal, get_deal, update_deal_status,
    set_deal_payment_details, calculate_seller_amounts,
    format_commission_info_seller, get_saved_payment_details,
    get_payment_detail_by_id, send_notification, DealStatus,
    is_admin, NUM_RE, get_user_statistics
)
from keyboards import admin_deposit_review_kb
from config import (
    LIMITS_PROMPT, ESCROW_BTC_ADDRESS, TTL_HOURS,
    ADMIN_IDS, ADMIN_CHAT_ID, SELL_ACCESS_MIN_SUCCESS, SELL_ACCESS_ALLOW_IDS
)
from utils import format_currency_amount, validate_payment_details, get_status_name, get_btc_rub_rate, btc_to_rub

router = Router()


# ==================== FSM СОСТОЯНИЯ ====================

class SellStates(StatesGroup):
    WAIT_LIMITS = State()
    WAIT_PAYMENT_DETAILS = State()

async def _can_user_sell(user_id: int) -> bool:
    if user_id in SELL_ACCESS_ALLOW_IDS or is_admin(user_id):
        return True
    stats = await get_user_statistics(user_id)
    success_count = stats.get("success_count", 0) if stats else 0
    return success_count >= SELL_ACCESS_MIN_SUCCESS


# ==================== НАЧАЛО СОЗДАНИЯ ЗАЯВКИ ====================

@router.callback_query(F.data == "sell:start")
async def sell_start(callback: CallbackQuery, state: FSMContext):
    """Начало создания заявки на продажу"""
    if not await _can_user_sell(callback.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sell_or_buy:menu")]
        ])
        text = (
            "🔒 <b>Продажа доступна после 100 успешных обменов</b>\n\n"
            "Если у вас есть вопросы, обратитесь в поддержку."
        )
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        return

    await state.set_state(SellStates.WAIT_LIMITS)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sell:cancel")]
    ])
    
    text = (
        "✨ Создание заявки на продажу BTC ✨\n\n"
        "📥 Укажите сумму в BTC:\n\n"
        "⚠️ Важно: Указывайте сумму только в BTC"
    )
    
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.message(SellStates.WAIT_LIMITS)
async def sell_limits(message: Message, state: FSMContext):
    """Получение лимитов от продавца (одна сумма или диапазон)"""
    import logging
    logger = logging.getLogger(__name__)
    
    # Логируем ВСЕ входящие сообщения в этом состоянии
    logger.info(f"🚨 ОБРАБОТЧИК sell_limits СРАБОТАЛ! Сообщение: '{message.text}', User ID: {message.from_user.id}")
    
    # Дополнительная проверка состояния для отладки
    current_state = await state.get_state()
    logger.info(f"🔍 Текущее состояние FSM: {current_state}")
    
    # Проверка на отмену
    if message.text and message.text.strip().lower() in ["отмена", "cancel", "❌ отмена"]:
        await state.clear()
        await message.answer(
            "❌ Создание заявки отменено",
        )
        return

    if not message.text:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sell:start")]
        ])
        await message.answer(
            "❌ <b>Нужно отправить сумму текстом</b>",
            parse_mode="HTML",
            reply_markup=kb
        )
        return
    
    # Сохраняем исходный текст для логирования
    original_text = message.text.strip()
    logger.info(f"🔍 Обработка ввода суммы для продажи: '{original_text}'")
    
    # Заменяем запятую на точку (для поддержки европейского формата)
    # Убираем пробелы и неразрывные пробелы
    text_clean = original_text.replace(",", ".").replace(" ", "").replace("\u00A0", "").strip()
    
    logger.info(f"🔍 Текст после очистки: '{text_clean}'")
    
    # Извлекаем числа с помощью регулярного выражения
    nums = NUM_RE.findall(text_clean.lower())
    
    logger.info(f"🔍 Извлеченные числа: {nums}")
    
    if not nums:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sell:start")]
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
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="sell:start")]
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
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sell:start")]
        ])
        await message.answer(
            "❌ <b>Некорректный формат</b>\n\n"
            "⚠️ <b>Используйте только числа и точку для десятичных дробей</b>\n\n"
            "💡 <b>Примеры:</b>\n"
            "• <code>0.005</code>\n"
            "• <code>0.001 - 0.10</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return
    except Exception as e:
        logger.error(f"Неожиданная ошибка при парсинге суммы: {e}", exc_info=True)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sell:start")]
        ])
        await message.answer(
            "❌ <b>Произошла непредвиденная ошибка при обработке суммы.</b>\n\n"
            "⚠️ Пожалуйста, попробуйте ввести сумму еще раз или свяжитесь с поддержкой.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return
    
    if min_btc <= 0 or max_btc <= 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sell:start")]
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

    logger.info(f"Финальные значения: min_btc={min_btc}, max_btc={max_btc}")

    # Рассчитываем комиссии
    try:
        seller_amounts = calculate_seller_amounts(max_btc)
        logger.info(f"Комиссии рассчитаны: {seller_amounts}")
    except Exception as e:
        logger.error(f"Ошибка при расчете комиссий: {e}", exc_info=True)
        await message.answer(
            "❌ <b>Ошибка при расчете комиссий</b>\n\n"
            "⚠️ Попробуйте позже",
            parse_mode="HTML"
        )
        return
    
    # Создаём сделку
    try:
        logger.info(f"Создание сделки: owner_id={message.from_user.id}, min_btc={min_btc}, max_btc={max_btc}, escrow_address={ESCROW_BTC_ADDRESS}")
        deal_id = await create_deal(
            owner_id=message.from_user.id,
            min_btc=min_btc,
            max_btc=max_btc,
            escrow_address=ESCROW_BTC_ADDRESS,
            side="SELL",
            status=DealStatus.AWAITING_DEPOSIT
        )
        logger.info(f"Сделка создана с ID: {deal_id}")
    except Exception as e:
        logger.error(f"Ошибка при создании сделки: {e}", exc_info=True)
        await message.answer(
            f"❌ <b>Ошибка создания заявки</b>\n\n"
            f"⚠️ {str(e)}\n\n"
            "Попробуйте позже",
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    if not deal_id:
        logger.error("Сделка не была создана (deal_id is None)")
        await message.answer(
            "❌ <b>Ошибка создания заявки</b>\n\n"
            "⚠️ Попробуйте позже",
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    # Формируем информацию о сумме
    if min_btc == max_btc:
        amount_info = f"💱 <b>Сумма:</b> {min_btc:.8f} BTC\n"
    else:
        amount_info = f"💱 <b>Диапазон:</b> {min_btc:.8f} - {max_btc:.8f} BTC\n"

    # Получаем курс BTC к рублям и рассчитываем сумму в рублях
    btc_rub_rate = await get_btc_rub_rate()
    net_to_seller_rub = btc_to_rub(seller_amounts['net_to_seller'], btc_rub_rate)
    net_to_seller_rub_formatted = f"{net_to_seller_rub:,.0f}".replace(",", " ")
    
    # Форматируем сумму депозита для команды /withdraw (убираем лишние нули)
    deposit_str = f"{seller_amounts['deposit']:.8f}".rstrip('0').rstrip('.')
    
    text = (
        f"✨ Заявка почти создана! ✨\n\n"
        f"💰 Внесите депозит: {seller_amounts['deposit']:.8f} BTC\n\n"
        f"🔗 BTC адрес для депозита: <b>{ESCROW_BTC_ADDRESS}</b>\n\n"
        f"💸 Вы получите после успешной сделки: {net_to_seller_rub_formatted} руб\n\n"
        f"⚠️ Важно: Для активации заявки необходимо внести депозит на указанный адрес в BTC!\n\n"
        f"⚠️ Срок действия сделки: {TTL_HOURS} часов\n\n"
        f"⚡️ Скопируй и вставь: /withdraw {ESCROW_BTC_ADDRESS} {deposit_str}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Депозит внесён", callback_data=f"sell:deposit_confirmed:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"sell:cancel_deal:{deal_id}")]
    ])
    
    try:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        logger.info(f"✅ Сообщение о создании сделки отправлено пользователю {message.from_user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке сообщения о создании сделки: {e}", exc_info=True)
        # Пытаемся отправить простое сообщение об ошибке
        try:
            await message.answer(
                f"✅ <b>Заявка создана!</b>\n\n"
                f"🆔 <b>ID заявки:</b> <code>{deal_id}</code>\n\n"
                f"⚠️ Произошла ошибка при отправке детальной информации. Проверьте заявку в разделе 'Мои сделки'.",
                parse_mode="HTML"
            )
        except Exception as e2:
            logger.error(f"❌ Критическая ошибка - не удалось отправить даже простое сообщение: {e2}")
    
    try:
        await state.clear()  # Очищаем состояние после создания сделки
        logger.info(f"✅ Состояние FSM очищено для пользователя {message.from_user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке состояния FSM: {e}", exc_info=True)


@router.message(SellStates.WAIT_PAYMENT_DETAILS)
async def seller_payment_details(message: Message, state: FSMContext):
    """Получение реквизитов от продавца"""
    import logging
    logger = logging.getLogger(__name__)
    
    data = await state.get_data()
    deal_id = data.get("deal_id")
    
    logger.info(f"Обработка реквизитов: deal_id={deal_id}, user_id={message.from_user.id}")
    
    if not deal_id:
        logger.error("deal_id не найден в состоянии FSM")
        await message.answer("❌ Ошибка: не найдена активная сделка. Пожалуйста, начните заново.")
        await state.clear()
        return
    
    # Проверяем, что сделка существует и принадлежит пользователю
    deal = await get_deal(deal_id)
    if not deal:
        logger.error(f"Сделка {deal_id} не найдена в базе данных")
        await message.answer("❌ Ошибка: сделка не найдена. Пожалуйста, начните заново.")
        await state.clear()
        return
    
    if deal["owner_id"] != message.from_user.id:
        logger.error(f"Попытка изменить реквизиты чужой сделки: deal_id={deal_id}, user_id={message.from_user.id}, owner_id={deal['owner_id']}")
        await message.answer("❌ Ошибка: это не ваша сделка.")
        await state.clear()
        return
    
    # Разрешаем ввод реквизитов для сделок в статусах AWAITING_DEPOSIT, CONFIRMING или ESCROWED
    if deal["status"] not in [DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        logger.warning(f"Попытка ввода реквизитов для сделки в неактуальном статусе: deal_id={deal_id}, status={deal['status']}")
        await message.answer("❌ Ошибка: неактуальная сделка. Статус сделки изменился.")
        await state.clear()
        return
    
    if not message.text:
        await message.answer(
            "❌ <b>Укажите реквизиты текстом</b>",
            parse_mode="HTML"
        )
        return

    payment_details = message.text.strip()
    
    if not payment_details or len(payment_details) < 5:
        await message.answer(
            "❌ <b>Реквизиты слишком короткие</b>\n\n"
            "⚠️ Укажите корректные реквизиты (минимум 5 символов)",
            parse_mode="HTML"
        )
        return
    
    # Проверяем валидность реквизитов
    if not validate_payment_details(payment_details):
        await message.answer(
            "❌ <b>Некорректные реквизиты</b>\n\n"
            "⚠️ Укажите корректные реквизиты для получения оплаты",
            parse_mode="HTML"
        )
        return
    
    logger.info(f"Сохранение реквизитов для сделки {deal_id}")
    await set_deal_payment_details(deal_id, payment_details)
    
    # Обновляем статус в зависимости от текущего статуса
    if deal["status"] == DealStatus.AWAITING_DEPOSIT:
        # Если депозит еще не подтвержден, статус CONFIRMING (ожидание подтверждения депозита)
        await update_deal_status(deal_id, DealStatus.CONFIRMING)
    elif deal["status"] == DealStatus.ESCROWED:
        # Если депозит уже подтвержден (ESCROWED), но реквизиты не были указаны - теперь указываем и делаем активной
        await update_deal_status(deal_id, DealStatus.ACTIVE)
        # Обновляем сообщение пользователю
        await message.answer(
            "✅ <b>Реквизиты сохранены!</b>\n\n"
            "🎉 <b>Сделка активирована!</b>\n\n"
            "Ваша заявка опубликована и доступна для покупки.",
            parse_mode="HTML"
        )
        await state.clear()
        return
    # Если статус CONFIRMING - ничего не меняем, просто сохраняем реквизиты и ждем подтверждения депозита
    
    # Уведомляем админов
    try:
        if ADMIN_CHAT_ID:
            deal = await get_deal(deal_id)
            kb = admin_deposit_review_kb(deal_id)
            
            from utils import get_btc_rub_rate, btc_to_rub
            from decimal import Decimal
            
            max_btc = Decimal(str(deal['max_btc']))
            # Форматируем BTC: убираем лишние нули
            if max_btc == max_btc.to_integral_value():
                # Целое число - показываем без десятичных знаков
                btc_formatted = f"{max_btc:.0f}"
            else:
                # Дробное число - показываем с нужным количеством знаков (убираем лишние нули)
                btc_str = f"{max_btc:.8f}".rstrip('0').rstrip('.')
                btc_formatted = btc_str
            
            # Добавляем сумму в рублях
            btc_rub_rate = await get_btc_rub_rate()
            rub_amount = btc_to_rub(max_btc, btc_rub_rate)
            rub_formatted = f"{rub_amount:,.0f}".replace(",", " ")
            
            admin_notification_text = (
                f"🔔 <b>Новая заявка на продажу</b>\n\n"
                f"👤 <b>Продавец:</b> @{message.from_user.username or 'без username'}\n"
                f"💰 <b>Сумма:</b> {btc_formatted} BTC ({rub_formatted} руб)\n"
                f"💳 <b>Реквизиты:</b> {payment_details}\n\n"
                f"⚠️ <b>Проверьте депозит и подтвердите сделку</b>"
            )
            await message.bot.send_message(
                ADMIN_CHAT_ID,
                admin_notification_text,
                reply_markup=kb,
        parse_mode="HTML"
    )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления админам: {e}", exc_info=True)
    
    # Обновляем данные сделки после сохранения реквизитов
    updated_deal = await get_deal(deal_id)
    logger.info(f"📊 Статус сделки {deal_id} после сохранения реквизитов: {updated_deal.get('status')}")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои сделки", callback_data="mydeals:back")]
    ])
    
    await message.answer(
        f"✅ <b>Реквизиты сохранены!</b> ✅\n\n"
        f"🆔 <b>Сделка #{deal_id}</b>\n"
        f"📊 <b>Статус:</b> {get_status_name(updated_deal.get('status'))}\n\n"
        "⏳ <b>Ожидайте подтверждения депозита администратором.</b>\n"
        "🔔 <b>Вы получите уведомление, когда депозит будет подтвержден.</b>\n\n"
        "💡 <b>Вы можете отслеживать статус сделки в разделе 'Мои сделки' → 'Активные сделки'.</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await state.clear()
    logger.info(f"Реквизиты успешно сохранены для сделки {deal_id} (новые реквизиты), статус: {updated_deal.get('status')}")


@router.callback_query(F.data == "sell:cancel")
async def sell_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена создания заявки на продажу"""
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


@router.callback_query(F.data.startswith("sell:cancel_payment:"))
async def sell_cancel_payment(callback: CallbackQuery, state: FSMContext):
    """Отмена ввода реквизитов"""
    deal_id = int(callback.data.split(":")[2])
    await state.clear()
    await update_deal_status(deal_id, DealStatus.CANCELLED)
    
    await callback.message.edit_text(
        "❌ Ввод реквизитов отменен\n\n"
        "⚠️ Сделка отменена",
        reply_markup=None
    )
    await callback.message.answer(
        "💼 Главное меню"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sell:cancel_deal:"))
async def sell_cancel_deal(callback: CallbackQuery, state: FSMContext):
    """Отмена сделки"""
    deal_id = int(callback.data.split(":")[2])
    await state.clear()
    await update_deal_status(deal_id, DealStatus.CANCELLED)
    
    await callback.message.edit_text(
        "❌ Сделка отменена",
        reply_markup=None
    )
    await callback.message.answer(
        "💼 Главное меню"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sell:deposit_confirmed:"))
async def sell_deposit_confirmed(callback: CallbackQuery, state: FSMContext):
    """Продавец подтвердил внесение депозита"""
    deal_id = int(callback.data.split(":")[2])
    
    deal = await get_deal(deal_id)
    if not deal:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return
    
    # Проверяем, что сделка принадлежит пользователю
    if deal["owner_id"] != callback.from_user.id:
        await callback.answer("❌ Это не ваша сделка", show_alert=True)
        return
    
    # Если реквизиты уже указаны, не запрашиваем повторно
    if deal.get("payment_details"):
        await callback.answer("✅ Реквизиты уже указаны", show_alert=True)
        return
    
    # Разрешаем ввод реквизитов для сделок без реквизитов и в статусах AWAITING_DEPOSIT, CONFIRMING или ESCROWED
    if deal["status"] not in [DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        await callback.answer("❌ Неактуальная сделка", show_alert=True)
        return
    
    # Обновляем статус сделки на CONFIRMING, если он еще AWAITING_DEPOSIT
    if deal["status"] == DealStatus.AWAITING_DEPOSIT:
        await update_deal_status(deal_id, DealStatus.CONFIRMING)
    
    # Отправляем уведомление о подтверждении депозита ТОЛЬКО если депозит действительно подтвержден системой
    # Депозит подтвержден, если статус ESCROWED или ACTIVE
    from utils import get_status_name
    from database import send_notification
    
    # Получаем актуальный статус сделки
    updated_deal = await get_deal(deal_id)
    if updated_deal:
        # Проверяем, что депозит действительно подтвержден (статус ESCROWED или ACTIVE)
        if updated_deal.get('status') in [DealStatus.ESCROWED, DealStatus.ACTIVE]:
            if updated_deal.get('status') == DealStatus.ACTIVE:
                notification_text = (
                    f"✅ <b>Депозит подтверждён!</b>\n\n"
                    f"Сделка #{deal_id} активирована.\n"
                    f"Теперь ваша заявка видна другим пользователям."
                )
            else:  # ESCROWED
                notification_text = (
                    f"✅ <b>Депозит подтверждён!</b>\n\n"
                    f"Сделка #{deal_id}.\n"
                    f"Статус: ✅ Депозит подтверждён"
                )
            
            try:
                await send_notification(callback.message.bot, callback.from_user.id, notification_text, reply_markup=None)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Ошибка отправки уведомления о подтверждении депозита: {e}", exc_info=True)
        else:
            # Депозит еще не подтвержден - уведомление не отправляем
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"⚠️ Депозит для сделки #{deal_id} еще не подтвержден системой (статус: {updated_deal.get('status')}), уведомление не отправляется")
    
    # Отвечаем на callback сразу, чтобы убрать загрузку
    await callback.answer()
    
    # Проверяем, есть ли у продавца сохраненные реквизиты
    saved_details = await get_saved_payment_details(callback.from_user.id)
    
    if saved_details:
        # Если есть сохраненные реквизиты, предлагаем выбрать
        buttons = []
        for detail in saved_details:
            buttons.append([InlineKeyboardButton(text=detail["title"], callback_data=f"sell:use_payment:{deal_id}:{detail['id']}")])
        buttons.append([InlineKeyboardButton(text="➕ Ввести новые реквизиты", callback_data=f"sell:enter_new_payment:{deal_id}")])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"sell:cancel_payment:{deal_id}")])
        
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        try:
            await callback.message.edit_text(
                "💳 <b>Выберите реквизиты для получения оплаты</b>\n\n"
                "📋 <b>Или создайте новые:</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}", exc_info=True)
            await callback.message.answer(
                "💳 <b>Выберите реквизиты для получения оплаты</b>\n\n"
                "📋 <b>Или создайте новые:</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        await state.set_state(SellStates.WAIT_PAYMENT_DETAILS) # Устанавливаем состояние для выбора/ввода реквизитов
        await state.update_data(deal_id=deal_id)
    else:
        # Если нет сохраненных, сразу просим ввести новые
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"sell:cancel_payment:{deal_id}")]
        ])
        
        try:
            await callback.message.edit_text(
                "✨ <b>Введите реквизиты</b> ✨\n\n"
                "💳 <b>Укажите ваши реквизиты для получения оплаты:</b>\n"
                "• Банковская карта\n"
                "• Банковский счет\n"
                "• USDT кошелек\n"
                "• Другие способы получения",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}", exc_info=True)
            await callback.message.answer(
                "✨ <b>Введите реквизиты</b> ✨\n\n"
                "💳 <b>Укажите ваши реквизиты для получения оплаты:</b>\n"
                "• Банковская карта\n"
                "• Банковский счет\n"
                "• USDT кошелек\n"
                "• Другие способы получения",
                reply_markup=kb,
                parse_mode="HTML"
            )
        await state.set_state(SellStates.WAIT_PAYMENT_DETAILS)
        await state.update_data(deal_id=deal_id)


@router.callback_query(F.data.startswith("sell:use_payment:"))
async def sell_use_payment(callback: CallbackQuery, state: FSMContext):
    """Использование сохраненных реквизитов"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        await callback.answer()  # Сразу отвечаем на callback, чтобы убрать загрузку
        
        parts = callback.data.split(":")
        logger.info(f"Обработка sell:use_payment: callback_data={callback.data}, parts={parts}")
        
        if len(parts) < 4:
            logger.error(f"Недостаточно частей в callback_data: {parts}")
            await callback.message.answer("❌ Ошибка: неверный формат данных. Пожалуйста, попробуйте снова.")
            return
        
        deal_id = int(parts[2])
        detail_id = int(parts[3])
        
        logger.info(f"Извлечены: deal_id={deal_id}, detail_id={detail_id}")
        
        deal = await get_deal(deal_id)
        if not deal:
            logger.error(f"Сделка {deal_id} не найдена")
            await callback.message.answer("❌ Сделка не найдена")
            return
        
        # Проверяем, что сделка принадлежит пользователю
        if deal["owner_id"] != callback.from_user.id:
            logger.error(f"Попытка использовать реквизиты для чужой сделки: deal_id={deal_id}, user_id={callback.from_user.id}, owner_id={deal['owner_id']}")
            await callback.message.answer("❌ Это не ваша сделка")
            return
        
        # Разрешаем использование реквизитов для сделок в статусах AWAITING_DEPOSIT, CONFIRMING или ESCROWED
        if deal["status"] not in [DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING, DealStatus.ESCROWED]:
            logger.warning(f"Попытка использовать реквизиты для сделки в неактуальном статусе: deal_id={deal_id}, status={deal['status']}")
            await callback.message.answer("❌ Неактуальная сделка. Статус сделки изменился.")
            return
        
        detail = await get_payment_detail_by_id(detail_id)
        if not detail:
            logger.error(f"Реквизиты {detail_id} не найдены")
            await callback.message.answer("❌ Реквизиты не найдены")
            return
        
        if detail["user_id"] != callback.from_user.id:
            logger.error(f"Попытка использовать чужие реквизиты: detail_id={detail_id}, user_id={callback.from_user.id}, detail_user_id={detail['user_id']}")
            await callback.message.answer("❌ Реквизиты не принадлежат вам")
            return
    
        logger.info(f"Сохранение реквизитов для сделки {deal_id}: {detail['title']}")
        # Сохраняем и название (банк/кошелёк), и сами реквизиты — иначе в сделке отображаются только цифры
        full_details = f"{detail['title']}\n{detail['details']}"
        await set_deal_payment_details(deal_id, full_details)
        
        # Обновляем статус в зависимости от текущего статуса
        if deal["status"] == DealStatus.AWAITING_DEPOSIT:
            # Если депозит еще не подтвержден, статус CONFIRMING (ожидание подтверждения депозита)
            await update_deal_status(deal_id, DealStatus.CONFIRMING)
        elif deal["status"] == DealStatus.ESCROWED:
            # Если депозит уже подтвержден (ESCROWED), но реквизиты не были указаны - теперь указываем и делаем активной
            await update_deal_status(deal_id, DealStatus.ACTIVE)
            # Обновляем сообщение пользователю
            try:
                await callback.message.edit_text(
                    f"✅ <b>Реквизиты сохранены!</b> ✅\n\n"
                    f"📋 <b>Используется:</b> {detail['title']}\n\n"
                    "🎉 <b>Сделка активирована!</b>\n\n"
                    "Ваша заявка опубликована и доступна для покупки.",
                    parse_mode="HTML"
                )
            except Exception:
                await callback.message.answer(
                    f"✅ <b>Реквизиты сохранены!</b> ✅\n\n"
                    f"📋 <b>Используется:</b> {detail['title']}\n\n"
                    "🎉 <b>Сделка активирована!</b>\n\n"
                    "Ваша заявка опубликована и доступна для покупки.",
                    parse_mode="HTML"
                )
            await state.clear()
            logger.info(f"Сделка {deal_id} активирована после указания реквизитов")
            return
        # Если статус CONFIRMING - ничего не меняем, просто сохраняем реквизиты и ждем подтверждения депозита
        
        # Уведомляем админов о новой сделке, ожидающей подтверждения депозита
        try:
            if ADMIN_CHAT_ID:
                from utils import get_btc_rub_rate, btc_to_rub
                from decimal import Decimal
                
                max_btc = Decimal(str(deal['max_btc']))
                # Форматируем BTC: убираем лишние нули
                if max_btc == max_btc.to_integral_value():
                    # Целое число - показываем без десятичных знаков
                    btc_formatted = f"{max_btc:.0f}"
                else:
                    # Дробное число - показываем с нужным количеством знаков (убираем лишние нули)
                    btc_str = f"{max_btc:.8f}".rstrip('0').rstrip('.')
                    btc_formatted = btc_str
                
                # Добавляем сумму в рублях
                btc_rub_rate = await get_btc_rub_rate()
                rub_amount = btc_to_rub(max_btc, btc_rub_rate)
                rub_formatted = f"{rub_amount:,.0f}".replace(",", " ")
                
                admin_notification_text = (
                    f"🔔 <b>Новая сделка ожидает подтверждения депозита!</b>\n\n"
                    f"🆔 <b>ID сделки:</b> <code>{deal_id}</code>\n"
                    f"👤 <b>Продавец:</b> @{callback.from_user.username or 'без username'} (ID: {callback.from_user.id})\n"
                    f"💰 <b>Сумма:</b> {btc_formatted} BTC ({rub_formatted} руб)\n"
                    f"💳 <b>Реквизиты:</b> <code>{detail['details']}</code>\n\n"
                    f"⚠️ <b>Проверьте депозит и подтвердите сделку</b>"
                )
                kb = admin_deposit_review_kb(deal_id)
                bot = callback.bot
                await bot.send_message(ADMIN_CHAT_ID, admin_notification_text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления админам: {e}", exc_info=True)
        
        # Обновляем данные сделки после сохранения реквизитов
        updated_deal = await get_deal(deal_id)
        logger.info(f"📊 Статус сделки {deal_id} после сохранения реквизитов: {updated_deal.get('status')}")
        
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои сделки", callback_data="mydeals:back")]
            ])
            
            await callback.message.edit_text(
                f"✅ <b>Реквизиты сохранены!</b> ✅\n\n"
                f"📋 <b>Используется:</b> {detail['title']}\n\n"
                f"🆔 <b>Сделка #{deal_id}</b>\n"
                f"📊 <b>Статус:</b> {get_status_name(updated_deal.get('status'))}\n\n"
                "⏳ <b>Ожидайте подтверждения депозита администратором.</b>\n"
                "🔔 <b>Вы получите уведомление, когда депозит будет подтвержден.</b>\n\n"
                "💡 <b>Вы можете отслеживать статус сделки в разделе 'Мои сделки' → 'Активные сделки'.</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}", exc_info=True)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои сделки", callback_data="mydeals:back")]
            ])
            await callback.message.answer(
                f"✅ <b>Реквизиты сохранены!</b> ✅\n\n"
                f"📋 <b>Используется:</b> {detail['title']}\n\n"
                f"🆔 <b>Сделка #{deal_id}</b>\n"
                f"📊 <b>Статус:</b> {get_status_name(updated_deal.get('status'))}\n\n"
                "⏳ <b>Ожидайте подтверждения депозита администратором.</b>\n"
                "🔔 <b>Вы получите уведомление, когда депозит будет подтвержден.</b>\n\n"
                "💡 <b>Вы можете отслеживать статус сделки в разделе 'Мои сделки' → 'Активные сделки'.</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        
        await state.clear()
        logger.info(f"Реквизиты успешно сохранены для сделки {deal_id}, статус: {updated_deal.get('status')}")
        
    except ValueError as e:
        logger.error(f"Ошибка парсинга callback_data: {e}, callback_data={callback.data}", exc_info=True)
        await callback.message.answer("❌ Ошибка: неверный формат данных. Пожалуйста, попробуйте снова.")
    except Exception as e:
        logger.error(f"Неожиданная ошибка в sell_use_payment: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка при сохранении реквизитов. Пожалуйста, попробуйте снова.")


@router.callback_query(F.data.startswith("sell:enter_new_payment:"))
async def sell_enter_new_payment(callback: CallbackQuery, state: FSMContext):
    """Ввод новых реквизитов"""
    logger = logging.getLogger(__name__)
    
    try:
        await callback.answer()  # Сразу отвечаем на callback
        
        deal_id = int(callback.data.split(":")[2])
        logger.info(f"Обработка sell:enter_new_payment: deal_id={deal_id}")
        
        deal = await get_deal(deal_id)
        if not deal:
            logger.error(f"Сделка {deal_id} не найдена")
            await callback.message.answer("❌ Сделка не найдена")
            return
        
        # Проверяем, что сделка принадлежит пользователю
        if deal["owner_id"] != callback.from_user.id:
            logger.error(f"Попытка ввести реквизиты для чужой сделки: deal_id={deal_id}, user_id={callback.from_user.id}")
            await callback.message.answer("❌ Это не ваша сделка")
            return
        
        # Разрешаем ввод реквизитов для сделок в статусах AWAITING_DEPOSIT, CONFIRMING или ESCROWED
        if deal["status"] not in [DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING, DealStatus.ESCROWED]:
            logger.warning(f"Попытка ввести реквизиты для сделки в неактуальном статусе: deal_id={deal_id}, status={deal['status']}")
            await callback.message.answer("❌ Неактуальная сделка. Статус сделки изменился.")
            return
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"sell:cancel_payment:{deal_id}")]
        ])
        
        try:
            await callback.message.edit_text(
                "✨ <b>Введите реквизиты</b> ✨\n\n"
                "💳 <b>Укажите ваши реквизиты для получения оплаты:</b>\n"
                "• Банковская карта\n"
                "• Банковский счет\n"
                "• USDT кошелек\n"
                "• Другие способы получения",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}", exc_info=True)
            await callback.message.answer(
                "✨ <b>Введите реквизиты</b> ✨\n\n"
                "💳 <b>Укажите ваши реквизиты для получения оплаты:</b>\n"
                "• Банковская карта\n"
                "• Банковский счет\n"
                "• USDT кошелек\n"
                "• Другие способы получения",
                reply_markup=kb,
                parse_mode="HTML"
            )
        
        await state.set_state(SellStates.WAIT_PAYMENT_DETAILS)
        await state.update_data(deal_id=deal_id)
        logger.info(f"Состояние установлено для ввода реквизитов: deal_id={deal_id}")
        
    except ValueError as e:
        logger.error(f"Ошибка парсинга deal_id: {e}, callback_data={callback.data}", exc_info=True)
        await callback.message.answer("❌ Ошибка: неверный формат данных. Пожалуйста, попробуйте снова.")
    except Exception as e:
        logger.error(f"Неожиданная ошибка в sell_enter_new_payment: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка. Пожалуйста, попробуйте снова.")


@router.callback_query(F.data.startswith("sell:check:"))
async def sell_check(callback: CallbackQuery, state: FSMContext):
    """Проверка депозита продавцом"""
    deal_id = int(callback.data.split(":")[2])
    
    deal = await get_deal(deal_id)
    if not deal:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return
    
    if deal["status"] != DealStatus.AWAITING_DEPOSIT:
        await callback.answer("⚠️ Статус сделки изменился", show_alert=True)
        return
    
    # Проверяем, есть ли сохраненные реквизиты
    saved = await get_saved_payment_details(callback.from_user.id)
    
    if saved:
        # Показываем список сохраненных реквизитов
        buttons = []
        for detail in saved:
            buttons.append([InlineKeyboardButton(
                text=f"💳 {detail['title']}",
                callback_data=f"sell:use_payment:{deal_id}:{detail['id']}"
            )])
        buttons.append([InlineKeyboardButton(
            text="➕ Новые реквизиты",
            callback_data=f"sell:new_payment:{deal_id}"
        )])
        buttons.append([InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"sell:cancel_payment:{deal_id}"
        )])
        
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback.message.edit_text(
            "💳 <b>Выберите реквизиты для получения оплаты</b>\n\n"
            "📋 <b>Или создайте новые:</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await state.set_state(SellStates.WAIT_PAYMENT_DETAILS)
        await state.update_data(deal_id=deal_id)
    else:
        # Если нет сохраненных, сразу просим ввести новые
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"sell:cancel_payment:{deal_id}")]
        ])
        
        await callback.message.edit_text(
            "✨ <b>Введите реквизиты</b> ✨\n\n"
            "💳 <b>Укажите ваши реквизиты для получения оплаты:</b>\n"
            "• Банковская карта\n"
            "• Банковский счет\n"
            "• USDT кошелек\n"
            "• Другие способы получения",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await state.set_state(SellStates.WAIT_PAYMENT_DETAILS)
        await state.update_data(deal_id=deal_id)
    
    await callback.answer()


@router.callback_query(F.data.startswith("sell:new_payment:"))
async def sell_new_payment(callback: CallbackQuery, state: FSMContext):
    """Запрос новых реквизитов"""
    deal_id = int(callback.data.split(":")[2])
    await state.set_state(SellStates.WAIT_PAYMENT_DETAILS)
    await state.update_data(deal_id=deal_id)
    
    await callback.answer()
    
    # Просим ввести новые реквизиты
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"sell:view:{deal_id}")]
    ])
    
    await callback.message.answer(
        "💳 <b>Введите новые реквизиты для получения оплаты:</b>\n\n"
        "📝 <b>Примеры:</b>\n"
        "• Номер карты: 1234 5678 9012 3456\n"
        "• Номер телефона: +79991234567\n"
        "• Другие способы получения",
        reply_markup=kb,
        parse_mode="HTML"
    )
