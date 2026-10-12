from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import BaseFilter

class HasAwaitingPartnerUsernameDeal(BaseFilter):
    async def __call__(self, message: Message) -> bool | dict:
        user_id = message.from_user.id
        from database import get_user_active_deals
        from config import DealStatus
        deals = await get_user_active_deals(user_id)
        target_deal = None
        for deal in deals:
            if deal["status"] == DealStatus.AWAITING_PARTNER_USERNAME and deal["owner_id"] == user_id:
                target_deal = deal
                break
        if target_deal:
            return {"target_deal": target_deal}
        return False

from aiogram.filters import BaseFilter

class HasAwaitingPartnerUsernameDeal(BaseFilter):
    async def __call__(self, message: Message) -> bool | dict:
        user_id = message.from_user.id
        from database import get_user_active_deals
        from config import DealStatus
        deals = await get_user_active_deals(user_id)
        target_deal = None
        for deal in deals:
            if deal["status"] == DealStatus.AWAITING_PARTNER_USERNAME and deal["owner_id"] == user_id:
                target_deal = deal
                break
        if target_deal:
            return {"target_deal": target_deal}
        return False

import logging
from decimal import Decimal
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from database import (
    get_deal, update_deal_status, get_user_active_deals,
    find_user_by_username, get_user
)
from utils import get_btc_rub_rate, btc_to_rub, get_status_name
from config import DealStatus, BOT_TOKEN


from aiogram.filters import BaseFilter

class HasAwaitingPartnerUsernameDeal(BaseFilter):
    async def __call__(self, message: Message) -> bool | dict:
        user_id = message.from_user.id
        deals = await get_user_active_deals(user_id)
        target_deal = None
        for deal in deals:
            if deal["status"] == DealStatus.AWAITING_PARTNER_USERNAME and deal["owner_id"] == user_id:
                target_deal = deal
                break
        if target_deal:
            return {"target_deal": target_deal}
        return False

logger = logging.getLogger(__name__)
router = Router()

def _fmt_btc(amount: Decimal) -> str:
    return f"{amount:.8f}".rstrip("0").rstrip(".")

def _escape(text: str) -> str:
    from aiogram.utils.markdown import html_decoration
    return html_decoration.quote(text) if text else ""
# Обработчик отмены создания сделки
@router.callback_query(F.data.startswith("garant_auto:cancel_creation:"))
async def garant_auto_cancel_creation(callback: CallbackQuery):
    deal_id = int(callback.data.split(":")[2])
    deal = await get_deal(deal_id)

    if not deal:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return

    if deal["owner_id"] != callback.from_user.id:
        await callback.answer("❌ Нет прав", show_alert=True)
        return

    if deal["status"] != DealStatus.AWAITING_PARTNER_USERNAME:
        await callback.answer("❌ Сделка уже в другом статусе", show_alert=True)
        return

    # Переводим в Cancelled и запускаем возврат
    await update_deal_status(deal_id, DealStatus.CANCELLED)

    # Запускаем возврат
    from withdraw_queue import enqueue_payout
    amount_str = str(deal["max_btc"])
    user = await get_user(deal["owner_id"])
    wallet = user.get("btc_wallet") if user else None

    await enqueue_payout(
        deal_id=deal_id,
        user_id=deal["owner_id"],
        wallet=wallet or "",
        amount=amount_str,
        payout_type="refund"
    )

    await callback.message.edit_text(
        f"❌ Создание сделки #{deal_id} отменено.\n\n"
        f"Сумма {amount_str} BTC отправлена в очередь на возврат."
    )
    await callback.answer()

# Обработчик ввода username (перехватываем любой текст)
from aiogram.filters import BaseFilter

class HasAwaitingPartnerUsernameDeal(BaseFilter):
    async def __call__(self, message: Message) -> bool | dict:
        user_id = message.from_user.id
        deals = await get_user_active_deals(user_id)
        target_deal = None
        for deal in deals:
            if deal["status"] == DealStatus.AWAITING_PARTNER_USERNAME and deal["owner_id"] == user_id:
                target_deal = deal
                break
        if target_deal:
            return {"target_deal": target_deal}
        return False

@router.message(F.text, HasAwaitingPartnerUsernameDeal())
async def process_partner_username(message: Message, state: FSMContext, target_deal: dict):
    deal_id = target_deal["id"]
    amount = Decimal(str(target_deal["max_btc"]))
    amount_str = _fmt_btc(amount)

    username = message.text.strip().lstrip("@")

    # Найти пользователя в БД
    partner = await find_user_by_username(username)
    if not partner:
        await message.answer(
            f"❌ Пользователь @{username} не найден или не зарегистрирован в боте.\n\n"
            "Пожалуйста, проверьте username и попросите пользователя запустить бота командой /start, затем отправьте username снова."
        )
        return

    partner_id = partner["tg_id"]
    user_id = message.from_user.id
    if partner_id == user_id:
        await message.answer("❌ Вы не можете создать сделку с самим собой. Укажите другой username.")
        return

    # Сохраняем партнера и переводим статус
    from database import db
    async with db() as conn:
        await conn.execute("UPDATE deals SET partner_id=? WHERE id=?", (partner_id, deal_id))
        await conn.commit()

    await update_deal_status(deal_id, DealStatus.AWAITING_PARTNER_ACCEPT)

    # Уведомляем создателя
    await message.answer(
        f"✅ Предложение сделки отправлено пользователю @{username}.\n"
        "Ожидаем его решения."
    )

    # Отправляем предложение контрагенту
    bot = message.bot
    creator_username = message.from_user.username or "Пользователь"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять сделку", callback_data=f"garant_auto:accept:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отказаться", callback_data=f"garant_auto:reject:{deal_id}")]
    ])

    msg_text = (
        "🤝 <b>ВАМ ПРЕДЛОЖЕНА P2P-СДЕЛКА</b>\n\n"
        f"ID сделки: <b>#{deal_id}</b>\n"
        f"Создатель: <b>@{creator_username}</b>\n"
        f"Сумма: <b>{amount_str} BTC</b>\n\n"
        "Вы согласны участвовать в сделке?"
    )

    try:
        await bot.send_message(partner_id, msg_text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить предложение контрагенту {partner_id}: {e}")
        await message.answer(f"⚠️ Не удалось отправить сообщение @{username}. Возможно, он заблокировал бота.")

# Обработка ответа контрагента: Отказаться
@router.callback_query(F.data.startswith("garant_auto:reject:"))
async def garant_auto_reject(callback: CallbackQuery):
    deal_id = int(callback.data.split(":")[2])
    deal = await get_deal(deal_id)

    if not deal:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return

    if deal["partner_id"] != callback.from_user.id:
        await callback.answer("❌ Это не ваше предложение", show_alert=True)
        return

    if deal["status"] != DealStatus.AWAITING_PARTNER_ACCEPT:
        await callback.answer("❌ Сделка уже в другом статусе", show_alert=True)
        return

    # Переводим в Cancelled
    await update_deal_status(deal_id, DealStatus.CANCELLED)

    amount_str = _fmt_btc(Decimal(str(deal["max_btc"])))

    # Меняем сообщение у контрагента
    await callback.message.edit_text(
        f"❌ Вы отказались от сделки #{deal_id} на сумму {amount_str} BTC."
    )

    # Запускаем возврат
    from withdraw_queue import enqueue_payout
    owner = await get_user(deal["owner_id"])
    wallet = owner.get("btc_wallet") if owner else None

    await enqueue_payout(
        deal_id=deal_id,
        user_id=deal["owner_id"],
        wallet=wallet or "",
        amount=amount_str,
        payout_type="refund"
    )

    # Уведомляем создателя
    try:
        await callback.bot.send_message(
            deal["owner_id"],
            f"❌ <b>СДЕЛКА ОТКЛОНЕНА</b>\n\n"
            f"Второй участник отказался от предложения.\n\n"
            f"ID сделки: <b>#{deal_id}</b>\n"
            f"Сумма: <b>{amount_str} BTC</b>\n\n"
            "Возврат средств инициирован.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить создателя {deal['owner_id']}: {e}")

    await callback.answer("Вы отказались от сделки.")

# Обработка ответа контрагента: Принять
@router.callback_query(F.data.startswith("garant_auto:accept:"))
async def garant_auto_accept(callback: CallbackQuery):
    deal_id = int(callback.data.split(":")[2])
    deal = await get_deal(deal_id)

    if not deal:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return

    if deal["partner_id"] != callback.from_user.id:
        await callback.answer("❌ Это не ваше предложение", show_alert=True)
        return

    if deal["status"] != DealStatus.AWAITING_PARTNER_ACCEPT:
        await callback.answer("❌ Сделка уже в другом статусе", show_alert=True)
        return

    # Переводим в MATCHED (соответствует логике Авто-гаранта)
    await update_deal_status(deal_id, DealStatus.MATCHED)

    from database import db
    from datetime import datetime
    async with db() as conn:
        await conn.execute(
            "UPDATE deals SET matched_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), deal_id),
        )
        await conn.commit()

    amount = Decimal(str(deal["max_btc"]))
    amount_str = _fmt_btc(amount)

    # Получаем курс для отображения
    rate = await get_btc_rub_rate()
    rub_str = f"{btc_to_rub(amount, rate):,.0f}".replace(",", " ")

    # Уведомляем контрагента
    await callback.message.edit_text(
        f"🛡 <b>Автогарант #{deal_id}</b>\n\n"
        f"Сделка принята.\n"
        f"💰 {amount_str} BTC (~{rub_str} руб)\n\n"
        "Отправьте фиат инициатору.\n"
        "Когда он подтвердит получение, BTC придёт на кошелёк из вашего профиля.",
        parse_mode="HTML"
    )

    # Уведомляем создателя
    from handlers.garant_handlers import _owner_decision_kb
    partner_username = callback.from_user.username or "пользователь"

    try:
        await callback.bot.send_message(
            deal["owner_id"],
            f"✅ <b>Второй участник (@{partner_username}) принял предложение сделки #{deal_id}</b>\n\n"
            f"💰 {amount_str} BTC (~{rub_str} руб)\n\n"
            "Когда фиат будет у вас — подтвердите получение.\n"
            "После этого BTC уйдёт второму участнику на кошелёк из его профиля.\n"
            "Если оплаты нет — оспорьте, депозит останется заблокирован.",
            reply_markup=_owner_decision_kb(deal_id),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить создателя {deal['owner_id']}: {e}")

    await callback.answer("Сделка принята.")

# Добавляем обработчик запроса отмены создателем (когда сделка активна, т.е. MATCHED)
@router.callback_query(F.data.startswith("garant_auto:request_cancel:"))
async def garant_auto_request_cancel(callback: CallbackQuery):
    deal_id = int(callback.data.split(":")[2])
    deal = await get_deal(deal_id)
    if not deal or deal["owner_id"] != callback.from_user.id or deal["status"] != DealStatus.MATCHED:
        await callback.answer("❌ Нельзя отменить", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Запросить отмену", callback_data=f"garant_auto:confirm_cancel:{deal_id}")],
        [InlineKeyboardButton(text="↩️ Вернуться к сделке", callback_data=f"mydeal:view:{deal_id}")]
    ])

    await callback.message.edit_text(
        "⚠️ <b>ОТМЕНА СДЕЛКИ</b>\n\n"
        f"Вы хотите запросить отмену сделки <b>#{deal_id}</b>.\n\n"
        "Второму участнику будет отправлено уведомление с просьбой подтвердить отмену.\n"
        "До получения ответа средства останутся зарезервированными.",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("garant_auto:confirm_cancel:"))
async def garant_auto_confirm_cancel(callback: CallbackQuery):
    deal_id = int(callback.data.split(":")[2])
    deal = await get_deal(deal_id)

    if not deal:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return

    if deal["owner_id"] != callback.from_user.id:
        await callback.answer("❌ Нет прав на отмену этой сделки", show_alert=True)
        return

    if deal["status"] != DealStatus.MATCHED:
        await callback.answer("❌ Эту сделку нельзя отменить в данный момент", show_alert=True)
        return

    # Проверка, нет ли уже ожидающего запроса на отмену
    from database import db
    async with db() as conn:
        # Проверяем не запросил ли уже отмену кто-то другой (в нашем случае может только создатель)
        # Просто переводим в CANCELLATION_REQUESTED
        await update_deal_status(deal_id, DealStatus.CANCELLATION_REQUESTED)

    # Отправляем уведомление второму участнику
    amount_str = _fmt_btc(Decimal(str(deal["max_btc"])))
    creator_username = callback.from_user.username or "создатель"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, отменить сделку", callback_data=f"garant_auto:cancel_accept:{deal_id}")],
        [InlineKeyboardButton(text="⚖️ Нет, открыть спор", callback_data=f"garant_auto:cancel_dispute:{deal_id}")]
    ])

    msg_text = (
        "🔔 <b>ЗАПРОС НА ОТМЕНУ СДЕЛКИ</b>\n\n"
        f"Создатель сделки @{creator_username} запросил отмену сделки <b>#{deal_id}</b>.\n\n"
        f"💰 Сумма: <b>{amount_str} BTC</b>\n\n"
        "Согласны ли вы отменить сделку?"
    )

    try:
        await callback.bot.send_message(
            deal["partner_id"],
            msg_text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить запрос на отмену партнеру {deal['partner_id']}: {e}")

    # Изменяем сообщение у создателя
    try:
        await callback.message.edit_text(
            f"⏳ Запрос на отмену сделки #{deal_id} отправлен второму участнику.\n"
            "До получения ответа средства останутся зарезервированными."
        )
    except Exception:
        await callback.answer("Запрос отправлен.", show_alert=True)


# Пре-подтверждение отмены от создателя, если требуется двойное подтверждение (опционально, но в задании сказано про подтверждение)
# Изменим обработчик request_cancel, чтобы он сначала показывал подтверждение, а отправлял запрос по другой кнопке

# Обработка ответа контрагента на запрос отмены: Согласие
@router.callback_query(F.data.startswith("garant_auto:cancel_accept:"))
async def garant_auto_cancel_accept(callback: CallbackQuery):
    deal_id = int(callback.data.split(":")[2])
    deal = await get_deal(deal_id)

    if not deal:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return

    if deal["partner_id"] != callback.from_user.id:
        await callback.answer("❌ Это не ваша сделка", show_alert=True)
        return

    if deal["status"] != DealStatus.CANCELLATION_REQUESTED:
        await callback.answer("❌ Запрос на отмену уже обработан или не актуален", show_alert=True)
        return

    # Согласие на отмену -> статус CANCELLED и запуск возврата
    await update_deal_status(deal_id, DealStatus.CANCELLED)

    amount_str = _fmt_btc(Decimal(str(deal["max_btc"])))

    # Меняем сообщение у контрагента
    await callback.message.edit_text(
        f"✅ <b>ОТМЕНА СОГЛАСОВАНА</b>\n\n"
        f"Сделка #{deal_id} отменена по согласию обеих сторон.\n"
        f"Возврат средств инициатору запущен.",
        parse_mode="HTML"
    )

    # Запускаем возврат
    from withdraw_queue import enqueue_payout
    owner = await get_user(deal["owner_id"])
    wallet = owner.get("btc_wallet") if owner else None

    await enqueue_payout(
        deal_id=deal_id,
        user_id=deal["owner_id"],
        wallet=wallet or "",
        amount=amount_str,
        payout_type="refund"
    )

    # Уведомляем создателя
    try:
        await callback.bot.send_message(
            deal["owner_id"],
            f"✅ <b>ОТМЕНА СОГЛАСОВАНА</b>\n\n"
            f"Сделка #{deal_id} отменена по согласию обеих сторон.\n\n"
            f"💰 Сумма возврата: <b>{amount_str} BTC</b>\n\n"
            "Возврат инициирован. Средства поступят в ближайшее время.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить создателя {deal['owner_id']}: {e}")

    await callback.answer("Отмена подтверждена.")

# Обработка ответа контрагента на запрос отмены: Открыть спор
@router.callback_query(F.data.startswith("garant_auto:cancel_dispute:"))
async def garant_auto_cancel_dispute(callback: CallbackQuery):
    deal_id = int(callback.data.split(":")[2])
    deal = await get_deal(deal_id)

    if not deal:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return

    if deal["partner_id"] != callback.from_user.id:
        await callback.answer("❌ Это не ваша сделка", show_alert=True)
        return

    if deal["status"] != DealStatus.CANCELLATION_REQUESTED:
        await callback.answer("❌ Запрос на отмену уже обработан или не актуален", show_alert=True)
        return

    # Несогласие -> статус DISPUTED
    await update_deal_status(deal_id, DealStatus.DISPUTED)

    # Обновляем статистику
    from database import update_user_stats_on_dispute
    await update_user_stats_on_dispute(deal["owner_id"])
    if deal.get("partner_id"):
        await update_user_stats_on_dispute(deal["partner_id"])

    # Меняем сообщение у контрагента
    await callback.message.edit_text(
        f"⚖️ <b>ОТКРЫТ СПОР</b>\n\n"
        f"По сделке #{deal_id} открыт спор.\n"
        "Средства остаются зарезервированными до разрешения спора.\n\n"
        "Ожидайте дальнейших инструкций от Авто-гаранта.",
        parse_mode="HTML"
    )

    # Уведомляем создателя
    try:
        await callback.bot.send_message(
            deal["owner_id"],
            f"⚖️ <b>ОТКРЫТ СПОР</b>\n\n"
            f"По сделке #{deal_id} открыт спор.\n"
            "Второй участник не согласен с отменой.\n"
            "Средства остаются зарезервированными до разрешения спора.\n\n"
            "Ожидайте дальнейших инструкций от Авто-гаранта.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить создателя {deal['owner_id']}: {e}")

    await callback.answer("Спор открыт.")
