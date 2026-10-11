"""
Обработчики действий со сделками (старт, оплата, подтверждение, отмена, споры)
"""
import asyncio
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from database import (
    get_deal, update_deal_status, claim_deal_exclusive,
    send_system_message, send_notification, calculate_cancellation_refund,
    update_user_stats_on_cancel, update_user_stats_on_dispute,
    update_daily_stats, DealStatus, db, is_admin, inc_success,
    update_user_stats_on_deal_complete
)
from keyboards import (
    start_or_back_kb, paid_or_cancel_kb, seller_verify_kb,
    admin_release_review_kb, rate_partner_kb, deal_actions_kb
)
from config import ADMIN_IDS, ADMIN_CHAT_ID
from utils import (
    get_status_name, get_status_emoji, get_role_name, format_datetime,
    format_deal_summary, is_seller, calculate_time_left, safe_edit_message
)

router = Router()

_TERMINAL_CANCEL_STATUSES = {
    DealStatus.RELEASED,
    DealStatus.CANCELLED,
    DealStatus.DISPUTED,
}


async def _block_cancel_if_closed(callback: CallbackQuery, d: dict, *, as_message: bool = False) -> bool:
    """True, если отмена запрещена (сделка уже закрыта)."""
    status = d.get("status")
    if status not in _TERMINAL_CANCEL_STATUSES:
        return False
    if status == DealStatus.RELEASED:
        text = "❌ Сделка уже завершена. Отменить её нельзя."
    elif status == DealStatus.CANCELLED:
        text = "❌ Сделка уже отменена."
    else:
        text = "❌ По сделке открыт спор. Отмена недоступна."
    if as_message:
        try:
            await callback.message.answer(text)
        except Exception:
            pass
        try:
            await callback.answer()
        except Exception:
            pass
    else:
        await callback.answer(text, show_alert=True)
    return True


# ==================== ПРОСМОТР ЗАЯВКИ ====================

@router.callback_query(F.data.startswith("deal:view:"))
async def deal_view(callback: CallbackQuery):
    """Просмотр заявки"""
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("❌ Неверный формат команды", show_alert=True)
            return
        deal_id = int(parts[2])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d or d.get("status") != DealStatus.ACTIVE:
        await callback.answer("Заявка недоступна", show_alert=True)
        return
    
    side = d["side"]
    min_btc = Decimal(d["min_btc"])
    max_btc = Decimal(d["max_btc"])
    exp = datetime.fromisoformat(d["expires_at"])
    exp_str = exp.strftime("%H:%M")
    time_left = calculate_time_left(d["expires_at"])
    
    from database import calculate_seller_amounts, calculate_buyer_amounts, get_user, get_user_statistics
    from utils import format_rating_stars, get_btc_rub_rate, btc_to_rub
    
    # Получаем информацию о владельце заявки
    owner = await get_user(d["owner_id"])
    owner_stats = await get_user_statistics(d["owner_id"]) if owner else None
    
    # Показываем информацию о комиссиях и сумме в рублях
    seller_amounts = calculate_seller_amounts(max_btc)
    buyer_amounts = calculate_buyer_amounts(max_btc)
    
    # Получаем курс BTC к рублям
    btc_rub_rate = await get_btc_rub_rate()
    
    lines = [
        f"📋 Заявка #{deal_id}",
        ""
    ]
    
    if side == "SELL":
        # Для заявок на продажу (когда покупатель смотрит заявку)
        # Информация о продавце
        if owner:
            display_name = owner.get("display_name")
            if display_name:
                lines.append(f"👤 Продавец: {display_name}")
            else:
                lines.append(f"👤 Продавец: ID {owner['tg_id']}")
            
            if owner_stats:
                rating = owner_stats.get("average_rating", 0)
                success_count = owner_stats.get("success_count", 0)
                total_deals = owner_stats.get("total_deals", 0)
                
                if rating > 0:
                    lines.append(f"⭐️ Рейтинг: {rating:.1f}/5.0")
                else:
                    lines.append("⭐️ Рейтинг: Нет оценок")
                
                if total_deals > 0:
                    success_rate = (success_count / total_deals * 100) if total_deals > 0 else 0
                    lines.append(f"✅ Успешных сделок: {success_count} ({success_rate:.0f}%)")
                else:
                    lines.append(f"✅ Успешных сделок: 0")
            
            lines.append("")
        
        # НОВАЯ ЛОГИКА КОМИССИИ:
        # 1. Покупатель платит продавцу: max_btc (полная сумма)
        # 2. Платформа удерживает 0% с суммы покупки (временно)
        # 3. Покупатель получает: max_btc - (max_btc * 0%) = max_btc * 1.0 (100% от суммы)
        # 4. Продавец получает: max_btc (полная сумма, которую платит покупатель)
        
        # Рассчитываем сумму к оплате для покупателя (базовая сумма, без доплат)
        buyer_payment_btc = buyer_amounts['buyer_payment']  # max_btc (полная сумма)
        buyer_payment_rub = btc_to_rub(buyer_payment_btc, btc_rub_rate)
        buyer_payment_rub_formatted = f"{buyer_payment_rub:,.0f}".replace(",", " ")
        
        # Рассчитываем сколько BTC получит покупатель (базовая сумма минус комиссия)
        buyer_receive_btc = buyer_amounts['buyer_receive']  # max_btc * 1.0 (100% при 0% комиссии)
        buyer_receive_rub = btc_to_rub(buyer_receive_btc, btc_rub_rate)
        buyer_receive_rub_formatted = f"{buyer_receive_rub:,.0f}".replace(",", " ")
        
        # Форматируем BTC сумму для отображения (убираем лишние нули)
        buyer_receive_btc_formatted = f"{buyer_receive_btc:.8f}".rstrip('0').rstrip('.')
        
        lines.append(f"💰 Сумма к оплате: {buyer_payment_rub_formatted} руб (Вы получите: {buyer_receive_btc_formatted} BTC ~ {buyer_receive_rub_formatted} руб)")
        lines.append(f"└Сумма с учетом комиссии*")
        lines.append("")
        # Реквизиты продавца НЕ показываем - они будут видны только после начала сделки
    else:
        # Для заявок на покупку (когда продавец смотрит заявку)
        # Информация о покупателе
        if owner:
            display_name = owner.get("display_name")
            if display_name:
                lines.append(f"👤 Покупатель: {display_name}")
            else:
                lines.append(f"👤 Покупатель: ID {owner['tg_id']}")
            
            if owner_stats:
                rating = owner_stats.get("average_rating", 0)
                success_count = owner_stats.get("success_count", 0)
                total_deals = owner_stats.get("total_deals", 0)
                
                if rating > 0:
                    lines.append(f"⭐️ Рейтинг: {rating:.1f}/5.0")
                else:
                    lines.append("⭐️ Рейтинг: Нет оценок")
                
                if total_deals > 0:
                    success_rate = (success_count / total_deals * 100) if total_deals > 0 else 0
                    lines.append(f"✅ Успешных сделок: {success_count} ({success_rate:.0f}%)")
                else:
                    lines.append(f"✅ Успешных сделок: 0")
            
            lines.append("")
        
        # Рассчитываем сколько получит продавец (полная сумма, которую платит покупатель)
        seller_receive_btc = max_btc  # Продавец получает полную сумму
        seller_receive_rub = btc_to_rub(seller_receive_btc, btc_rub_rate)
        seller_receive_rub_formatted = f"{seller_receive_rub:,.0f}".replace(",", " ")
        seller_receive_btc_formatted = f"{seller_receive_btc:.8f}".rstrip('0').rstrip('.')
        
        # Покупатель платит полную сумму max_btc
        buyer_payment_btc = max_btc
        buyer_payment_btc_formatted = f"{buyer_payment_btc:.8f}".rstrip('0').rstrip('.')
        
        lines.append(f"💸 Вы получите: {seller_receive_rub_formatted} руб")
        lines.append(f"└ Вы заплатите: {buyer_payment_btc_formatted} BTC")
        lines.append("")
        lines.append("❗️Важно: комиссия временно 0% для всех")
        lines.append("")
        lines.append("💼 BTC-кошелёк будет показан после старта сделки")
        lines.append("")
    
    # Время действия заявки
    lines.append(f"⏱️ Действует до: {exp_str}")
    
    # Создаём клавиатуру с дополнительными кнопками
    kb_rows = [
        [InlineKeyboardButton(text="🚀 Начать сделку", callback_data=f"deal:start:{deal_id}")]
    ]
    
    # Добавляем кнопку просмотра профиля контрагента
    if owner:
        kb_rows.append([InlineKeyboardButton(text="👤 Профиль контрагента", callback_data=f"profile:public:{d['owner_id']}:back:deal:view:{deal_id}")])
    
    kb_rows.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    
    await safe_edit_message(callback.message, "\n".join(lines), reply_markup=kb, parse_mode=None)
    await callback.answer()


# ==================== СТАРТ СДЕЛКИ (МАТЧИНГ) ====================

@router.callback_query(F.data.startswith("deal:start:"))
async def deal_start(callback: CallbackQuery, state: FSMContext):
    """Старт сделки (откликнуться на заявку)"""
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("❌ Неверный формат команды", show_alert=True)
            return
        deal_id = int(parts[2])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d or d.get("status") != DealStatus.ACTIVE:
        await callback.answer("Заявка недоступна", show_alert=True)
        return
    
    if d.get("owner_id") == callback.from_user.id:
        await callback.answer("Нельзя откликаться на свою заявку", show_alert=True)
        return

    # Показываем подтверждение перед началом сделки
    from database import get_user, get_user_statistics, calculate_buyer_amounts, calculate_seller_amounts
    from utils import get_btc_rub_rate, btc_to_rub
    from decimal import Decimal
    
    owner = await get_user(d["owner_id"])
    owner_stats = await get_user_statistics(d["owner_id"]) if owner else None
    
    # Рассчитываем сумму к оплате с учетом комиссии
    max_btc = Decimal(d["max_btc"])
    buyer_amounts = calculate_buyer_amounts(max_btc)
    seller_amounts = calculate_seller_amounts(max_btc)
    buyer_payment_btc = buyer_amounts['buyer_payment']  # НОВАЯ ЛОГИКА: базовая сумма без доплат
    
    # Получаем курс и рассчитываем сумму в рублях
    btc_rub_rate = await get_btc_rub_rate()
    buyer_payment_rub = btc_to_rub(buyer_payment_btc, btc_rub_rate)
    buyer_payment_rub_formatted = f"{buyer_payment_rub:,.0f}".replace(",", " ")
    
    # НОВАЯ ЛОГИКА: Покупатель платит базовую сумму, получает 100% (комиссия 0% временно)
    buyer_receive_btc = buyer_amounts['buyer_receive']  # max_btc * 1.0 (100% при 0% комиссии)
    buyer_receive_rub = btc_to_rub(buyer_receive_btc, btc_rub_rate)
    buyer_receive_rub_formatted = f"{buyer_receive_rub:,.0f}".replace(",", " ")
    buyer_receive_btc_formatted = f"{buyer_receive_btc:.8f}".rstrip('0').rstrip('.')
    
    confirm_text = (
        f"⚠️ Подтверждение сделки #{deal_id}\n\n"
        f"💰 Сумма к оплате: {buyer_payment_rub_formatted} руб (Вы получите: {buyer_receive_btc_formatted} BTC ~ {buyer_receive_rub_formatted} руб)\n"
        f"└Сумма с учетом комиссии*\n\n"
    )
    
    if owner:
        display_name = owner.get("display_name")
        if display_name:
            confirm_text += f"👤 Продавец: {display_name}\n"
        else:
            confirm_text += f"👤 Продавец: ID {owner['tg_id']}\n"
        
        if owner_stats:
            success_count = owner_stats.get("success_count", 0)
            if success_count > 0:
                confirm_text += f"✅ Успешных сделок: {success_count}\n"
        
        confirm_text += "\n"
    
    confirm_text += "Вы уверены, что готовы начать сделку?"
    
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, начать", callback_data=f"deal:confirm_start:{deal_id}")],
        [InlineKeyboardButton(text="👤 Профиль контрагента", callback_data=f"profile:public:{d['owner_id']}:back:deal:start:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"deal:view:{deal_id}")]
    ])
    
    await safe_edit_message(callback.message, confirm_text, reply_markup=confirm_kb, parse_mode=None)
    await callback.answer()


@router.callback_query(F.data.startswith("deal:confirm_start:"))
async def deal_confirm_start(callback: CallbackQuery, state: FSMContext):
    """Подтверждение начала сделки"""
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("❌ Неверный формат команды", show_alert=True)
            return
        deal_id = int(parts[2])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d or d["status"] != DealStatus.ACTIVE:
        await callback.answer("Заявка недоступна", show_alert=True)
        return
    
    if d["owner_id"] == callback.from_user.id:
        await callback.answer("Нельзя откликаться на свою заявку", show_alert=True)
        return

    # Сохраняем время начала сделки (это момент, когда покупатель подтверждает начало)
    matched_at_time = datetime.utcnow()
    
    # Сохраняем время матча и закрепляем сумму в рублях
    from database import calculate_buyer_amounts
    from utils import get_btc_rub_rate, btc_to_rub
    
    # Рассчитываем сумму к оплате и конвертируем в рубли
    max_btc = Decimal(d.get("buy_amount") or d["max_btc"])
    buyer_amounts = calculate_buyer_amounts(max_btc)
    buyer_payment_btc = buyer_amounts['buyer_payment']  # НОВАЯ ЛОГИКА: базовая сумма без доплат
    
    # Получаем текущий курс и рассчитываем сумму в рублях
    btc_rub_rate = await get_btc_rub_rate()
    buyer_payment_rub = btc_to_rub(buyer_payment_btc, btc_rub_rate)
    locked_rub_amount = str(buyer_payment_rub)
    
    # Получаем кошелек покупателя из профиля, если он не указан в сделке
    buyer_id = callback.from_user.id
    buyer_wallet = d.get("buyer_wallet")
    
    if not buyer_wallet:
        # Получаем кошелек из профиля покупателя
        from database import get_user
        buyer_user = await get_user(buyer_id)
        if buyer_user and buyer_user.get("btc_wallet"):
            buyer_wallet = buyer_user["btc_wallet"]
    
    # Эксклюзивно захватываем сделку и одновременно устанавливаем matched_at, locked_rub_amount и buyer_wallet
    async with db() as conn:
        # Пытаемся захватить сделку и установить все необходимые поля одновременно
        cur = await conn.execute(
            "UPDATE deals SET status=?, partner_id=?, matched_at=?, locked_rub_amount=?, buyer_wallet=? WHERE id=? AND status=?",
            (DealStatus.MATCHED, buyer_id, matched_at_time.isoformat(), locked_rub_amount, buyer_wallet or d.get("buyer_wallet"), deal_id, DealStatus.ACTIVE)
        )
        await conn.commit()
        
        if cur.rowcount == 0:
            # Если сделка уже была захвачена, сообщаем об этом
            await callback.answer("Кто-то уже принял эту заявку", show_alert=True)
            return

    # Перезагружаем сделку, чтобы получить актуальные данные
    d = await get_deal(deal_id)

    # Системное сообщение в чат
    username = callback.from_user.username or "Пользователь"
    await send_system_message(deal_id, f"Контрагент @{username} присоединился к сделке")

    # 🤝 УВЕДОМЛЕНИЕ: Найден контрагент
    partner_username = callback.from_user.username or "Пользователь"
    notification = (
        "🤝 <b>Найден контрагент!</b>\n\n"
        f"Сделка #{deal_id}\n"
        f"Контрагент: @{partner_username}\n\n"
        f"Готовы {'продать' if d['side']=='BUY' else 'купить'} BTC.\n"
        "Следите за уведомлениями!"
    )
    await send_notification(callback.message.bot, d["owner_id"], notification)

    # Импортируем необходимые функции
    from database import calculate_buyer_amounts, calculate_seller_amounts
    from utils import get_btc_rub_rate, btc_to_rub
    from config import PAYMENT_TIMEOUT_MINUTES
    
    # Рассчитываем суммы
    max_btc = Decimal(d["max_btc"])
    buyer_amounts = calculate_buyer_amounts(max_btc)
    buyer_payment_btc = buyer_amounts['buyer_payment']  # НОВАЯ ЛОГИКА: базовая сумма без доплат
    
    # Получаем курс и рассчитываем сумму в рублях
    btc_rub_rate = await get_btc_rub_rate()
    buyer_payment_rub = btc_to_rub(buyer_payment_btc, btc_rub_rate)
    buyer_payment_rub_formatted = f"{buyer_payment_rub:,.0f}".replace(",", " ")
    
    # Рассчитываем время истечения оплаты (используем только что установленное время)
    payment_deadline = matched_at_time + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
    now = datetime.utcnow()
    time_remaining = payment_deadline - now
    
    if time_remaining.total_seconds() > 0:
        minutes_left = int(time_remaining.total_seconds() // 60)
        seconds_left = int(time_remaining.total_seconds() % 60)
        # Формат MM:SS для таймера
        time_left_str = f"{minutes_left:02d}:{seconds_left:02d}"
    else:
        time_left_str = "00:00"
    
    if d["side"] == "SELL":
        # Продажа: показываем реквизиты покупателю с полной информацией
        details = d.get("payment_details") or "Реквизиты пока не указаны. Ожидайте."
        
        # НОВАЯ ЛОГИКА: Покупатель платит базовую сумму, получает 100% (комиссия 0% временно)
        buyer_receive_btc = buyer_amounts['buyer_receive']  # max_btc * 1.0 (100% при 0% комиссии)
        buyer_receive_rub = btc_to_rub(buyer_receive_btc, btc_rub_rate)
        buyer_receive_rub_formatted = f"{buyer_receive_rub:,.0f}".replace(",", " ")
        
        deal_info_buyer = (
            f"🤝 <b>Сделка началась!</b>\n\n"
            f"💰 <b>Сумма к оплате:</b>\n"
            f"            <b>{buyer_payment_rub_formatted} руб</b>\n\n"
            f"⏰ <b>Время на оплату:</b> {time_left_str}\n\n"
            f"💳 <b>Реквизиты продавца для оплаты:</b>\n\n"
            f"<b>{details}</b>\n\n"
            f"⚠️ <b>Важно:</b> Переведите полную сумму, комиссия уже учтена!\n"
            f"После оплаты нажмите кнопку \"✅ Оплатил\""
        )
        
        # Сохраняем message_id уведомления о начале сделки для покупателя (в случае продажи)
        buyer_started_msg_id = callback.message.message_id
        await safe_edit_message(
            callback.message,
            deal_info_buyer,
            reply_markup=paid_or_cancel_kb(deal_id, d),
            parse_mode="HTML"
        )
        # Сохраняем message_id (используем текущее сообщение, так как оно редактируется)
        async with db() as conn:
            await conn.execute(
                "UPDATE deals SET buyer_deal_started_message_id=? WHERE id=?",
                (buyer_started_msg_id, deal_id)
            )
            await conn.commit()
        
        # Уведомляем продавца (владельца заявки)
        from keyboards import deal_actions_kb
        seller_kb = deal_actions_kb(d, d["owner_id"], is_owner=True, saved_payment_details=None)
        
        seller_info = (
            f"🤝 <b>Сделка началась!</b>\n\n"
            f"📋 <b>Сделка #{deal_id}</b>\n\n"
            f"💰 <b>Сумма сделки:</b>\n"
            f"   {max_btc:.8f} BTC\n"
            f"   ({buyer_payment_rub_formatted} руб)\n\n"
            f"💸 <b>Вы получите:</b>\n"
            f"   {buyer_payment_rub_formatted} руб\n\n"
            f"⏰ <b>Время на оплату покупателем:</b> {time_left_str}\n\n"
            f"✅ <b>Реквизиты отправлены покупателю.</b>\n"
            f"⏳ Ожидаем оплату от покупателя."
        )
        
        try:
            sent_msg = await callback.message.bot.send_message(
                d["owner_id"],
                seller_info,
                reply_markup=seller_kb,
                parse_mode="HTML"
            )
            # Сохраняем message_id уведомления о начале сделки для продавца
            async with db() as conn:
                await conn.execute(
                    "UPDATE deals SET seller_deal_started_message_id=? WHERE id=?",
                    (sent_msg.message_id, deal_id)
                )
                await conn.commit()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка отправки уведомления продавцу {d['owner_id']}: {e}", exc_info=True)
    else:
        # Покупка: продавец должен указать реквизиты
        if not d.get("payment_details"):
            from handlers.buy_handlers import BuyMatchStates
            await state.set_state(BuyMatchStates.WAIT_SELLER_DETAILS)
            await state.update_data(deal_id=deal_id)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:view:{deal_id}")]
            ])
            await safe_edit_message(
                callback.message,
                "Укажите ваши реквизиты для получения оплаты (банк/карта/и т. п.).",
                reply_markup=kb
            )
        else:
            # Покупка: реквизиты уже указаны, отправляем покупателю с полной информацией
            deal_info_buyer = (
                f"🤝 <b>Сделка началась!</b>\n\n"
                f"💰 <b>Сумма к оплате:</b>\n"
                f"            <b>{buyer_payment_rub_formatted} руб</b>\n\n"
                f"⏰ <b>Время на оплату:</b> {time_left_str}\n\n"
                f"💳 <b>Реквизиты продавца для оплаты:</b>\n\n"
                f"<b>{d['payment_details']}</b>\n\n"
                f"⚠️ <b>Важно:</b> Переведите полную сумму, комиссия уже учтена!\n"
                f"После оплаты нажмите кнопку \"✅ Оплатил\""
            )
            
            try:
                sent_msg_buyer = await callback.message.bot.send_message(
                    d["owner_id"],
                    deal_info_buyer,
                    reply_markup=paid_or_cancel_kb(deal_id, d),
                    parse_mode="HTML"
                )
                # Сохраняем message_id уведомления о начале сделки для покупателя
                async with db() as conn:
                    await conn.execute(
                        "UPDATE deals SET buyer_deal_started_message_id=? WHERE id=?",
                        (sent_msg_buyer.message_id, deal_id)
                    )
                    await conn.commit()
            except Exception:
                pass
            
            # Сообщение продавцу (тому, кто откликнулся)
            # Продавец получает то, что платит покупатель (max_btc * 1.025)
            from keyboards import deal_actions_kb
            seller_kb = deal_actions_kb(d, callback.from_user.id, is_owner=False, saved_payment_details=None)
            
            seller_info = (
                f"🤝 <b>Сделка началась!</b>\n\n"
                f"📋 <b>Сделка #{deal_id}</b>\n\n"
                f"💰 <b>Сумма сделки:</b>\n"
                f"   {max_btc:.8f} BTC\n"
                f"   ({buyer_payment_rub_formatted} руб)\n\n"
                f"💸 <b>Вы получите:</b>\n"
                f"   {buyer_payment_rub_formatted} руб\n\n"
                f"⏰ <b>Время на оплату покупателем:</b> {time_left_str}\n\n"
                f"✅ <b>Реквизиты отправлены покупателю.</b>\n"
                f"⏳ Ожидаем оплату от покупателя."
            )
            
            # Сохраняем message_id уведомления о начале сделки для продавца (в случае покупки)
            seller_started_msg_id = callback.message.message_id
            await safe_edit_message(
                callback.message,
                seller_info,
                reply_markup=seller_kb,
                parse_mode="HTML"
            )
            # Сохраняем message_id (используем текущее сообщение, так как оно редактируется)
            async with db() as conn:
                await conn.execute(
                    "UPDATE deals SET seller_deal_started_message_id=? WHERE id=?",
                    (seller_started_msg_id, deal_id)
                )
                await conn.commit()
    
    await callback.answer()


# ==================== ПОКУПАТЕЛЬ ОТМЕТИЛ ОПЛАТУ ====================

@router.callback_query(F.data.startswith("deal:paid:"))
async def deal_paid(callback: CallbackQuery):
    """Покупатель отметил оплату"""
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("❌ Неверный формат команды", show_alert=True)
            return
        deal_id = int(parts[2])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    # Определяем кто покупатель
    buyer_id = d["partner_id"] if d["side"] == "SELL" else d["owner_id"]
    
    if callback.from_user.id != buyer_id:
        await callback.answer("Кнопка только для покупателя", show_alert=True)
        return
    
    # Сохраняем время
    async with db() as conn:
        await conn.execute(
            "UPDATE deals SET payment_marked_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), deal_id)
        )
        await conn.commit()
    
    await update_deal_status(deal_id, DealStatus.PAYMENT_PENDING)
    
    # Обновляем deal для получения актуальных данных
    d = await get_deal(deal_id)
    
    # Системное сообщение в чат
    await send_system_message(deal_id, "Покупатель отметил оплату")
    
    # 💳 УВЕДОМЛЕНИЕ: Покупатель отметил оплату
    # Получаем сумму в рублях (закрепленную или рассчитываем)
    from database import calculate_buyer_amounts
    from utils import get_btc_rub_rate, btc_to_rub
    from decimal import Decimal
    
    max_btc = Decimal(d.get("buy_amount") or d["max_btc"])
    buyer_amounts = calculate_buyer_amounts(max_btc)
    buyer_payment_btc = buyer_amounts['buyer_payment']  # НОВАЯ ЛОГИКА: базовая сумма без доплат
    
    # Используем закрепленную сумму, если есть, иначе рассчитываем
    if d.get("locked_rub_amount"):
        rub_amount = Decimal(d["locked_rub_amount"])
    else:
        btc_rub_rate = await get_btc_rub_rate()
        rub_amount = btc_to_rub(buyer_payment_btc, btc_rub_rate)
    
    rub_formatted = f"{rub_amount:,.0f}".replace(",", " ")
    
    # Получаем реквизиты продавца
    payment_details = d.get("payment_details") or "Реквизиты не указаны"
    
    notification = (
        f"🪙 <b>Покупатель перевел вам рубли</b>\n\n"
        f"💸 <b>Сумма поступления: {rub_formatted} руб</b>\n\n"
        f"💳 <b>Ваши реквизиты:</b>\n"
        f"<b>{payment_details}</b>\n\n"
        f"⚠️ <b>Проверьте точное поступление средств на ваши реквизиты.\n"
        f"Подтвердите сделку или откройте диспут</b> ⚠️"
    )
    await send_notification(
        callback.message.bot,
        d["owner_id"],
        notification,
        reply_markup=seller_verify_kb(deal_id, d)
    )
    
    # Обновляем клавиатуру - убираем кнопку "Оплатил" (она больше не нужна)
    # Используем paid_or_cancel_kb с актуальными данными сделки (чтобы скрыть диспут для завершенных сделок)
    from keyboards import paid_or_cancel_kb
    kb = paid_or_cancel_kb(deal_id, d)
    # Добавляем кнопку "К сделке"
    kb.inline_keyboard.append([InlineKeyboardButton(text="◀️ К сделке", callback_data=f"mydeal:view:{deal_id}")])
    
    # Сохраняем message_id сообщения "Оплата отмечена" для последующего удаления
    paid_message_id = callback.message.message_id
    
    # Обновляем сообщение
    try:
        await callback.message.edit_text(
            "✅ Оплата отмечена. Ожидайте подтверждения продавцом.",
            reply_markup=kb
        )
        # Сохраняем message_id в базе данных
        async with db() as conn:
            await conn.execute(
                "UPDATE deals SET buyer_paid_message_id=? WHERE id=?",
                (paid_message_id, deal_id)
            )
            await conn.commit()
    except Exception as e:
        # Если не удалось отредактировать, отправляем новое сообщение
        sent_msg = await callback.message.answer(
            "✅ Оплата отмечена. Ожидайте подтверждения продавцом.",
            reply_markup=kb
        )
        # Сохраняем message_id нового сообщения
        async with db() as conn:
            await conn.execute(
                "UPDATE deals SET buyer_paid_message_id=? WHERE id=?",
                (sent_msg.message_id, deal_id)
            )
            await conn.commit()
    
    await callback.answer("OK")


# ==================== ПРОДАВЕЦ ПОДТВЕРДИЛ ОПЛАТУ ====================

def _is_seller(user_id: int, d: dict) -> bool:
    """Проверить является ли пользователь продавцом в сделке"""
    return (d["side"] == "SELL" and user_id == d["owner_id"]) or \
           (d["side"] == "BUY" and user_id == (d.get("partner_id") or -1))


@router.callback_query(F.data.startswith("seller:ok:"))
async def seller_ok(callback: CallbackQuery):
    """Продавец подтвердил оплату - автоматически выполняем релиз BTC"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("❌ Неверный формат команды", show_alert=True)
            return
        deal_id = int(parts[2])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена")
        return
    
    if not _is_seller(callback.from_user.id, d):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    
    # Сохраняем время подтверждения продавцом
    async with db() as conn:
        await conn.execute(
            "UPDATE deals SET seller_confirmed_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), deal_id)
        )
        await conn.commit()
    
    # Отвечаем пользователю сразу, чтобы он не ждал
    await callback.answer("✅ Оплата подтверждена, обрабатываю...")
    
    # Выполняем автоматический релиз BTC в фоне (не блокируем ответ)
    logger.info(f"🚀 [SELLER_OK] Начинаем автоматический релиз BTC для сделки {deal_id} после подтверждения оплаты продавцом")
    from database import auto_release_btc
    import asyncio
    
    # Запускаем автовывод в фоне ОДИН РАЗ
    # auto_release_btc теперь просто добавляет задачу в очередь, поэтому не блокирует
    async def background_release():
        try:
            logger.info(f"🚀 [SELLER_OK] Вызываю auto_release_btc для сделки {deal_id}")
            release_result = await auto_release_btc(deal_id, bot=callback.message.bot)
            logger.info(f"📊 [SELLER_OK] Результат автовывода для сделки {deal_id}: {release_result}")
            # Уведомления отправляются внутри auto_release_btc, не нужно дублировать здесь
        except Exception as e:
            logger.error(f"❌ [SELLER_OK] Ошибка при автовыводе для сделки {deal_id}: {e}", exc_info=True)
            release_result = {"success": False, "message": f"Ошибка: {str(e)}"}
    
    # Запускаем в фоне ОДИН РАЗ
    asyncio.create_task(background_release())
    
    # auto_release_btc теперь просто добавляет задачу в очередь и быстро возвращается
    # НЕ отправляем уведомления здесь - они будут отправлены после подтверждения вывода в complete_deal_after_withdrawal_confirmation
    # НЕ обновляем статус здесь - он будет обновлен после подтверждения вывода
    # Просто сообщаем пользователю, что задача добавлена в очередь
    await safe_edit_message(
        callback.message,
        f"⏳ <b>Обработка...</b>\n\n"
        f"✅ <b>Оплата подтверждена</b>\n\n"
        f"⏳ <b>Ожидаем подтверждение вывода BTC...</b>\n\n"
        f"Сделка будет завершена автоматически после подтверждения вывода.",
        parse_mode="HTML"
    )
    await callback.answer("✅ Оплата подтверждена, ожидаем подтверждение вывода")


@router.callback_query(F.data.startswith("seller:bad:"))
async def seller_bad(callback: CallbackQuery):
    """Продавец не увидел оплату"""
    deal_id = int(callback.data.split(":")[2])
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена")
        return
    
    if not _is_seller(callback.from_user.id, d):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    
    partner = d.get("partner_id") or d.get("buyer_id")
    if partner:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="ОТКРЫТЬ ДИСПУТ", callback_data=f"deal:dispute:{deal_id}")]
            ])
            await callback.message.bot.send_message(
                int(partner),
                "⚠️ Продавец не увидел оплату. Проверьте перевод и при необходимости откройте спор.",
                reply_markup=kb
            )
        except Exception:
            pass
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К сделке", callback_data=f"mydeal:view:{deal_id}")]
    ])
    await safe_edit_message(
        callback.message,
        "✅ Отмечено как неуспешно. Сообщили покупателю.",
        reply_markup=kb
    )
    await callback.answer("OK")


# ==================== ОТКРЫТИЕ СПОРА ====================

@router.callback_query(F.data.startswith("deal:dispute:"))
async def deal_dispute(callback: CallbackQuery):
    """Открытие спора"""
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("❌ Неверный формат команды", show_alert=True)
            return
        deal_id = int(parts[2])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    # Проверяем, не завершена ли уже сделка
    if d.get("status") == DealStatus.RELEASED:
        await callback.answer(
            "❌ Невозможно открыть спор!\n\n"
            "⚠️ Сделка уже успешно завершена.\n"
            "BTC переведены покупателю.",
            show_alert=True
        )
        return
    
    # Проверяем, не отменена ли уже сделка
    if d.get("status") == DealStatus.CANCELLED:
        await callback.answer(
            "❌ Невозможно открыть спор!\n\n"
            "⚠️ Сделка уже отменена.",
            show_alert=True
        )
        return
    
    await update_deal_status(deal_id, DealStatus.DISPUTED)
    
    # Системное сообщение в чат
    initiator_username = callback.from_user.username or "Пользователь"
    await send_system_message(deal_id, f"@{initiator_username} открыл спор. Администратор подключится к разбору.")
    
    # Обновляем статистику
    await update_user_stats_on_dispute(callback.from_user.id)
    await update_daily_stats()
    
    # Определяем второго участника
    if callback.from_user.id == d["owner_id"]:
        other_user_id = d.get("partner_id")
    else:
        other_user_id = d["owner_id"]
    
    # ⚠️ УВЕДОМЛЕНИЕ второму участнику
    if other_user_id:
        notification = (
            "⚠️ <b>По сделке открыт спор!</b>\n\n"
            f"Сделка #{deal_id}\n"
            f"Контрагент @{initiator_username} открыл спор.\n\n"
            "Администратор подключится к разбору ситуации.\n"
            "Вы можете использовать чат для общения."
        )
        await send_notification(callback.message.bot, other_user_id, notification)
    
    # ⚠️ УВЕДОМЛЕНИЕ админам
    targets = list(ADMIN_IDS)
    if ADMIN_CHAT_ID:
        targets.append(ADMIN_CHAT_ID)
    
    # Получаем информацию о сделке для уведомления
    from database import get_user
    owner = await get_user(d["owner_id"])
    partner = await get_user(d.get("partner_id")) if d.get("partner_id") else None
    
    owner_name = owner.get("display_name") if owner else f"ID {d['owner_id']}"
    partner_name = partner.get("display_name") if partner else (f"ID {d['partner_id']}" if d.get("partner_id") else "Нет")
    
    for tid in targets:
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔴 Просмотреть спор", callback_data=f"admin:dispute:view:{deal_id}")],
                [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"admin:dispute:chat:{deal_id}")]
            ])
            
            admin_notification = (
                "⚠️ <b>[DISPUTE] Открыт новый спор!</b>\n\n"
                f"🔴 <b>Сделка #{deal_id}</b>\n\n"
                f"👤 <b>Инициатор:</b> @{initiator_username} (ID: {callback.from_user.id})\n"
                f"👤 <b>Продавец:</b> {owner_name} (ID: {d['owner_id']})\n"
                f"👤 <b>Покупатель:</b> {partner_name} (ID: {d.get('partner_id', 'N/A')})\n\n"
                f"💡 <b>Используйте кнопки ниже для работы со спором</b>"
            )
            await callback.message.bot.send_message(tid, admin_notification, reply_markup=admin_kb, parse_mode="HTML")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка отправки уведомления админу {tid}: {e}", exc_info=True)
    
    deal_id = int(callback.data.split(":")[2])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
        [InlineKeyboardButton(text="◀️ К сделке", callback_data=f"mydeal:view:{deal_id}")]
    ])
    await safe_edit_message(
        callback.message,
        "⚠️ Спор открыт. Администратор подключится к разбору.\n\nВы можете использовать чат для общения.",
        reply_markup=kb
    )
    await callback.answer("OK")


# ==================== ОТМЕНА СДЕЛКИ ====================

# ВАЖНО: Более специфичный обработчик должен быть ПЕРЕД менее специфичным!
# Иначе deal_cancel перехватит все callback'и, включая deal:cancel:confirm:

@router.callback_query(F.data.startswith("deal:cancel:confirm:"))
async def deal_cancel_confirm(callback: CallbackQuery):
    """Подтверждение отмены сделки продавцом - выполняем фактическую отмену"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] === НАЧАЛО ОБРАБОТКИ ===")
    logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Callback data: {callback.data}")
    logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] User ID: {callback.from_user.id}")
    
    # СРАЗУ отвечаем пользователю, чтобы убрать загрузку
    await callback.answer("✅ Обрабатываю отмену...")
    
    try:
        parts = callback.data.split(":")
        logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Parts: {parts}")
        if len(parts) < 4:
            logger.error(f"❌ [DEAL_CANCEL_CONFIRM] Неверный формат команды, parts: {parts}")
            await callback.message.answer("❌ Неверный формат команды")
            return
        deal_id = int(parts[3])
        logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Parsed deal_id: {deal_id}")
    except (ValueError, IndexError, AttributeError) as e:
        logger.error(f"❌ [DEAL_CANCEL_CONFIRM] Ошибка парсинга deal_id: {e}", exc_info=True)
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("Сделка не найдена")
        return
    if await _block_cancel_if_closed(callback, d, as_message=True):
        return
    
    is_owner = callback.from_user.id == d["owner_id"]
    
    if not is_owner:
        await callback.message.answer("Недостаточно прав")
        return
    
    # Проверяем, что сделка еще не началась
    if d.get("status") in [DealStatus.MATCHED, DealStatus.PAYMENT_PENDING] and d.get("partner_id"):
        await callback.message.answer(
            "❌ Отмена невозможна!\n\n"
            "⚠️ Сделка уже началась."
        )
        return
    
    logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Сделка найдена: #{deal_id}, статус: {d.get('status')}, side: {d.get('side')}")
    
    # Выполняем отмену с возвратом депозита
    if d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED, DealStatus.ACTIVE, DealStatus.AWAITING_DEPOSIT]:
        logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Сделка требует возврата депозита")
        
        deposit_amount = Decimal(d["max_btc"]) if d["side"] == "SELL" else Decimal(d.get("buy_amount") or d["max_btc"])
        logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Сумма депозита: {deposit_amount} BTC")
        
        refund_calc = calculate_cancellation_refund(deposit_amount)
        logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Расчет возврата: {refund_calc}")
        
        # Форматируем числа для быстрого отображения
        deposit_formatted = f"{refund_calc['original_deposit']:.8f}".rstrip('0').rstrip('.')
        refund_formatted = f"{refund_calc['refund_amount']:.8f}".rstrip('0').rstrip('.')
        
        logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Отформатированные суммы: депозит={deposit_formatted}, возврат={refund_formatted}")
        
        # Сразу показываем сообщение пользователю (без ожидания операций с БД)
        refund_msg = (
            "❌ <b>Сделка отменена</b>\n\n"
            f"Сделка #{deal_id} отменена вами.\n\n"
            f"💰 <b>Возврата депозита:</b>\n"
            f"Депозит: {deposit_formatted} BTC\n"
            f"Штраф за отмену (-1.5%)\n"
            f"💸 К возврату: <b>{refund_formatted} BTC</b>\n\n"
            f"⏳ <b>Обрабатываю возврат депозита...</b>"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ К сделке", callback_data=f"mydeal:view:{deal_id}")]
        ])
        
        # Сразу обновляем сообщение (не блокируем)
        try:
            await callback.message.edit_text(refund_msg, reply_markup=kb, parse_mode="HTML")
            logger.info(f"🔴 [DEAL_CANCEL_CONFIRM] Сообщение пользователю обновлено")
        except Exception as e:
            logger.error(f"❌ [DEAL_CANCEL_CONFIRM] Ошибка обновления сообщения: {e}", exc_info=True)
        
        # Все тяжелые операции выполняем в фоне
        # refund_calc доступен через замыкание
        async def process_cancellation_in_background():
            import logging
            import json
            from pathlib import Path
            logger = logging.getLogger(__name__)
            
            logger.info(f"🔴 [PROCESS_CANCELLATION] === НАЧАЛО ФОНОВОЙ ОБРАБОТКИ ===")
            logger.info(f"🔴 [PROCESS_CANCELLATION] refund_calc доступен через замыкание: {refund_calc}")
            
            try:
                # Получаем кошелек пользователя
                from database import get_user
                user = await get_user(callback.from_user.id)
                user_wallet = user.get("btc_wallet") if user else None
                logger.info(f"🔴 [PROCESS_CANCELLATION] Кошелек пользователя: {user_wallet}")
                
                # Обновляем статус сделки
                logger.info(f"🔴 [PROCESS_CANCELLATION] Обновляю статус сделки на CANCELLED")
                await update_deal_status(deal_id, DealStatus.CANCELLED)
                
                # Обновляем статистику
                try:
                    await update_user_stats_on_cancel(callback.from_user.id)
                    await update_daily_stats()
                    logger.info(f"🔴 [PROCESS_CANCELLATION] Статистика обновлена")
                except Exception as e:
                    logger.error(f"❌ [PROCESS_CANCELLATION] Ошибка при обновлении статистики: {e}", exc_info=True)
                
                logger.info(f"💰 [ОТМЕНА] Начало возврата депозита для сделки {deal_id}, статус: {d['status']}, кошелек: {user_wallet}")
                
                # Автоматический возврат депозита через forwarder.py (добавляем в очередь)
                if user_wallet:
                    try:
                        amount_str = f"{refund_calc['refund_amount']:.8f}".rstrip('0').rstrip('.')
                        logger.info(f"💰 [ОТМЕНА] === СОЗДАНИЕ ЗАДАЧИ НА ВОЗВРАТ ДЕПОЗИТА ===")
                        logger.info(f"💰 [ОТМЕНА] Сделка #{deal_id}, пользователь {callback.from_user.id}")
                        logger.info(f"💰 [ОТМЕНА] Кошелек: {user_wallet}, сумма к возврату: {amount_str} BTC")
                        from withdraw_queue import enqueue_payout
                        queued = await enqueue_payout(
                            deal_id=deal_id,
                            user_id=callback.from_user.id,
                            wallet=user_wallet,
                            amount=amount_str,
                            payout_type="refund",
                        )
                        if queued.get("created"):
                            logger.info(f"✅ [ОТМЕНА] Задача возврата депозита сохранена (сделка #{deal_id})")
                        else:
                            logger.info(f"💰 [ОТМЕНА] Задача не добавлена (дубликат)")
                        
                        # Обновляем сообщение с финальным статусом
                        final_refund_status = "⏳ <b>Депозит возвращается...</b>\n\nДепозит будет возвращен автоматически."
                        final_msg = (
                            "❌ <b>Сделка отменена</b>\n\n"
                            f"Сделка #{deal_id} отменена вами.\n\n"
                            f"💰 <b>Возврата депозита:</b>\n"
                            f"Депозит: {deposit_formatted} BTC\n"
                            f"Штраф за отмену (-1.5%)\n"
                            f"💸 К возврату: <b>{refund_formatted} BTC</b>\n\n"
                            f"{final_refund_status}"
                        )
                        try:
                            await callback.message.edit_text(final_msg, reply_markup=kb, parse_mode="HTML")
                        except:
                            pass
                            
                    except Exception as e:
                        logger.error(f"❌ [ОТМЕНА] Ошибка при добавлении задачи возврата: {e}", exc_info=True)
                        import traceback
                        logger.error(f"❌ [ОТМЕНА] Traceback: {traceback.format_exc()}")
                        
                        final_refund_status = f"⚠️ <b>Ошибка автовозврата:</b> {str(e)}\n\nСредства будут возвращены администратором вручную."
                        final_msg = (
                            "❌ <b>Сделка отменена</b>\n\n"
                            f"Сделка #{deal_id} отменена вами.\n\n"
                            f"💰 <b>Возврата депозита:</b>\n"
                            f"Депозит: {deposit_formatted} BTC\n"
                            f"Штраф за отмену (-1.5%)\n"
                            f"💸 К возврату: <b>{refund_formatted} BTC</b>\n\n"
                            f"{final_refund_status}"
                        )
                        try:
                            await callback.message.edit_text(final_msg, reply_markup=kb, parse_mode="HTML")
                        except:
                            pass
                else:
                    logger.warning(f"⚠️ [ОТМЕНА] BTC кошелек не указан в профиле пользователя {callback.from_user.id}")
                    final_refund_status = "⚠️ <b>BTC кошелек не указан в профиле.</b>\n\nСредства будут возвращены администратором после указания кошелька."
                    final_msg = (
                        "❌ <b>Сделка отменена</b>\n\n"
                        f"Сделка #{deal_id} отменена вами.\n\n"
                        f"💰 <b>Возврата депозита:</b>\n"
                        f"Депозит: {deposit_formatted} BTC\n"
                        f"Штраф за отмену (-1.5%)\n"
                        f"💸 К возврату: <b>{refund_formatted} BTC</b>\n\n"
                        f"{final_refund_status}"
                    )
                    try:
                        await callback.message.edit_text(final_msg, reply_markup=kb, parse_mode="HTML")
                    except:
                        pass
                        
            except Exception as e:
                logger.error(f"❌ [ОТМЕНА] Критическая ошибка при обработке отмены: {e}", exc_info=True)
        
        # Запускаем обработку в фоне
        asyncio.create_task(process_cancellation_in_background())
        
        # Уведомляем партнёра (в фоне, не блокируем)
        async def notify_partner_in_background():
            try:
                partner_id = d.get("partner_id")
                if partner_id:
                    partner_notification = (
                        "❌ <b>Сделка отменена</b>\n\n"
                        f"Сделка #{deal_id}\n"
                        "Ваш контрагент отменил сделку.\n"
                        "Средства будут возвращены продавцу с удержанием штрафа 1.5%."
                    )
                    await send_notification(callback.message.bot, partner_id, partner_notification)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Ошибка уведомления партнёра: {e}", exc_info=True)
        
        asyncio.create_task(notify_partner_in_background())
    else:
        # Обычная отмена без возврата (только для владельца, если нет депозита)
        if is_owner:
            await update_deal_status(deal_id, DealStatus.CANCELLED)
        
        # Обновляем статистику
        await update_user_stats_on_cancel(callback.from_user.id)
        await update_daily_stats()
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="mydeals:back")]
        ])
        await safe_edit_message(
            callback.message,
            f"❌ Сделка #{deal_id} отменена.",
            reply_markup=kb
        )
        
        # Уведомляем партнёра
        partner_id = d.get("partner_id")
        if partner_id and partner_id != callback.from_user.id:
            partner_notification = (
                "❌ <b>Сделка отменена</b>\n\n"
                f"Сделка #{deal_id}\n"
                "Ваш контрагент отменил сделку."
            )
            await send_notification(callback.message.bot, partner_id, partner_notification)


@router.callback_query(F.data.startswith("deal:cancel:"))
async def deal_cancel(callback: CallbackQuery):
    """Отмена сделки - показываем подтверждение для продавца"""
    import logging
    logger = logging.getLogger(__name__)
    
    # Проверяем, что это не подтверждение отмены (deal:cancel:confirm:)
    callback_str = str(callback.data)
    if callback_str.startswith("deal:cancel:confirm:"):
        # Это подтверждение отмены - пропускаем, обработает deal_cancel_confirm
        return
    
    # Простой парсинг - берем все после "deal:cancel:"
    try:
        if not callback_str.startswith("deal:cancel:"):
            await callback.answer("❌ Неверный формат команды", show_alert=True)
            return
        
        # Извлекаем ID - все что после "deal:cancel:"
        deal_id_str = callback_str.replace("deal:cancel:", "").strip()
        
        if not deal_id_str:
            await callback.answer("❌ Неверный ID сделки", show_alert=True)
            return
        
        deal_id = int(deal_id_str)
        logger.info(f"[DEAL_CANCEL] Parsed deal_id: {deal_id} from callback: {callback_str}")
        
    except (ValueError, AttributeError) as e:
        logger.error(f"[DEAL_CANCEL] Ошибка парсинга: {e}, callback.data: {callback.data}")
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        logger.error(f"[DEAL_CANCEL] Сделка {deal_id} не найдена в базе")
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    if await _block_cancel_if_closed(callback, d):
        return
    
    allowed = {d["owner_id"], d.get("partner_id") or -1}
    is_admin_user = callback.from_user.id in ADMIN_IDS
    
    if not is_admin_user and callback.from_user.id not in allowed:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    
    is_owner = callback.from_user.id == d["owner_id"]
    is_partner = callback.from_user.id == d.get("partner_id")
    is_seller_user = _is_seller(callback.from_user.id, d)
    
    # Проверяем, не началась ли сделка (есть контрагент)
    # Если сделка началась (MATCHED, PAYMENT_PENDING), отмена возможна только через диспут
    if d.get("status") in [DealStatus.MATCHED, DealStatus.PAYMENT_PENDING] and d.get("partner_id"):
        await callback.answer(
            "❌ Отмена невозможна!\n\n"
            "⚠️ Сделка уже началась. Для отмены необходимо открыть спор.\n"
            "Администратор рассмотрит ваш запрос.",
            show_alert=True
        )
        return
    
    # Если это владелец заявки и есть депозит - показываем подтверждение с предупреждением о комиссии
    # Для заявки на продажу (SELL) депозит всегда есть (max_btc)
    # Для заявки на покупку (BUY) депозит есть только если сделка началась (есть partner_id)
    has_deposit = False
    if d["side"] == "SELL":
        # Для заявки на продажу депозит всегда есть
        has_deposit = True
        deposit_amount = Decimal(d["max_btc"])
    else:  # BUY
        # Для заявки на покупку депозит есть только если сделка началась
        if d.get("partner_id") and d.get("buy_amount"):
            has_deposit = True
            deposit_amount = Decimal(d["buy_amount"])
        else:
            # Для активной заявки на покупку депозита еще нет
            has_deposit = False
    
    # Если есть депозит - показываем подтверждение с расчетом возврата
    if is_owner and has_deposit:
        refund_calc = calculate_cancellation_refund(deposit_amount)
        
        # Получаем курс для отображения в рублях
        from utils import get_btc_rub_rate, btc_to_rub
        btc_rub_rate = await get_btc_rub_rate()
        deposit_rub = btc_to_rub(deposit_amount, btc_rub_rate)
        refund_rub = btc_to_rub(refund_calc['refund_amount'], btc_rub_rate)
        fee_rub = btc_to_rub(refund_calc['cancellation_fee'], btc_rub_rate)
        
        # Форматируем числа для отображения
        deposit_formatted = f"{deposit_amount:.8f}".rstrip('0').rstrip('.')
        refund_formatted = f"{refund_calc['refund_amount']:.8f}".rstrip('0').rstrip('.')
        fee_formatted = f"{refund_calc['cancellation_fee']:.8f}".rstrip('0').rstrip('.')
        deposit_rub_formatted = f"{deposit_rub:,.0f}".replace(",", " ")
        refund_rub_formatted = f"{refund_rub:,.0f}".replace(",", " ")
        fee_rub_formatted = f"{fee_rub:,.0f}".replace(",", " ")
        
        confirm_text = (
            f"⚠️ <b>Подтверждение отмены заявки</b> ⚠️\n\n"
            f"📋 <b>Заявка #{deal_id}</b>\n\n"
            f"❓ <b>Вы точно хотите отменить заявку?</b>\n\n"
            f"💰 <b>Расчёт возврата депозита:</b>\n"
            f"Депозит: {deposit_formatted} BTC (~{deposit_rub_formatted} руб)\n\n"
            f"📉 <b>Штраф за отмену (-1.5%):</b> {fee_formatted} BTC (~{fee_rub_formatted} руб)\n\n"
            f"💸 <b>К возврату:</b> {refund_formatted} BTC (~{refund_rub_formatted} руб)\n\n"
            f"⚠️ <b>Внимание:</b> При отмене заявки с вашего депозита будет удержан штраф 1.5%.\n"
            f"Оставшаяся сумма будет возвращена автоматически на ваш BTC-кошелёк."
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"deal:cancel:confirm:{deal_id}")],
            [InlineKeyboardButton(text="❌ Нет, вернуться", callback_data=f"mydeal:view:{deal_id}")]
        ])
        
        await safe_edit_message(callback.message, confirm_text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        return
    
    # Если второй участник (partner) пытается отменить сделку после её начала - запрещаем
    # Выход из сделки возможен только до начала (когда сделка еще ACTIVE)
    if is_partner and not is_owner:
        if d.get("status") in [DealStatus.MATCHED, DealStatus.PAYMENT_PENDING]:
            await callback.answer(
                "❌ Выход невозможен!\n\n"
                "⚠️ Сделка уже началась. Для отмены необходимо открыть спор.\n"
                "Администратор рассмотрит ваш запрос.",
                show_alert=True
            )
            return
        
        # Если сделка еще не началась (ACTIVE), можно выйти
        # Убираем второго участника из сделки и возвращаем в ACTIVE
        # Обновляем expires_at, чтобы сделка не истекла сразу
        from config import TTL_HOURS
        new_expires_at = (datetime.utcnow() + timedelta(hours=TTL_HOURS)).isoformat()
        
        async with db() as conn:
            await conn.execute(
                "UPDATE deals SET partner_id = NULL, matched_at = NULL, status = ?, expires_at = ? WHERE id = ?",
                (DealStatus.ACTIVE, new_expires_at, deal_id)
            )
            await conn.commit()
        
        # Обновляем статистику
        await update_daily_stats()
        
        # Системное сообщение в чат
        username = callback.from_user.username or "Пользователь"
        await send_system_message(deal_id, f"Контрагент @{username} вышел из сделки. Сделка снова доступна для других пользователей.")
        
        # Уведомляем владельца
        owner_notification = (
            "🔄 <b>Контрагент вышел из сделки</b>\n\n"
            f"Сделка #{deal_id}\n"
            f"Контрагент @{username} вышел из сделки.\n\n"
            "Ваша заявка снова активна и доступна для других пользователей."
        )
        await send_notification(callback.message.bot, d["owner_id"], owner_notification)
        
        # Сообщение второму участнику
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await safe_edit_message(
            callback.message,
            f"✅ <b>Вы вышли из сделки #{deal_id}</b>\n\n"
            "Сделка возвращена в активные и доступна для других пользователей.",
            reply_markup=kb
        )
        
        await callback.answer("Вы вышли из сделки")
        return
    
    # Если дошли сюда, значит это не продавец с депозитом или другая ситуация - выполняем отмену без подтверждения
    # (для покупателей или других случаев)
    # Для продавца с депозитом подтверждение уже показано выше и функция завершилась через return
    
    # Для других случаев (покупатель, нет депозита и т.д.) - выполняем отмену сразу
    if is_owner:
        await update_deal_status(deal_id, DealStatus.CANCELLED)
        
        # Обновляем статистику
        await update_user_stats_on_cancel(callback.from_user.id)
        await update_daily_stats()
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="mydeals:back")]
        ])
        await safe_edit_message(
            callback.message,
            f"❌ Сделка #{deal_id} отменена.",
            reply_markup=kb
        )
        
        # Уведомляем партнёра
        partner_id = d.get("partner_id")
        if partner_id and partner_id != callback.from_user.id:
            partner_notification = (
                "❌ <b>Сделка отменена</b>\n\n"
                f"Сделка #{deal_id}\n"
                "Ваш контрагент отменил сделку."
            )
            await send_notification(callback.message.bot, partner_id, partner_notification)
    else:
        await callback.answer("❌ Только владелец сделки может её отменить", show_alert=True)
        return
    
    await callback.answer("Отменено")


# ==================== ПРОСМОТР МОЕЙ СДЕЛКИ ====================

@router.callback_query(F.data.startswith("mydeal:view:"))
async def mydeal_view(callback: CallbackQuery):
    """Просмотр моей сделки"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            logger.error(f"[MYDEAL_VIEW] Неверный формат callback: {callback.data}")
            await callback.answer("❌ Неверный формат команды", show_alert=True)
            return
        deal_id = int(parts[2])
        logger.info(f"[MYDEAL_VIEW] Просмотр сделки {deal_id} пользователем {callback.from_user.id}")
    except (ValueError, IndexError, AttributeError) as e:
        logger.error(f"[MYDEAL_VIEW] Ошибка парсинга deal_id: {e}, callback.data: {callback.data}")
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        logger.error(f"[MYDEAL_VIEW] Сделка {deal_id} не найдена")
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    # Импортируем необходимые функции
    from database import get_user, get_user_statistics
    
    # Логируем для отладки таймера
    import logging
    logger = logging.getLogger(__name__)
    if d.get("status") in [DealStatus.MATCHED, DealStatus.PAYMENT_PENDING]:
        logger.info(f"Просмотр сделки {deal_id}: status={d.get('status')}, matched_at={d.get('matched_at')}, owner_id={d.get('owner_id')}, partner_id={d.get('partner_id')}, user_id={callback.from_user.id}")
        
        # Если matched_at не установлен для MATCHED/PAYMENT_PENDING сделки, устанавливаем его на текущее время
        # Это может быть для старых сделок, созданных до добавления этой функции
        if not d.get("matched_at") and d.get("partner_id"):
            logger.warning(f"matched_at не установлен для сделки {deal_id} со статусом {d.get('status')}. Устанавливаем текущее время.")
            matched_at_time = datetime.utcnow()
            async with db() as conn:
                await conn.execute(
                    "UPDATE deals SET matched_at=? WHERE id=?",
                    (matched_at_time.isoformat(), deal_id)
                )
                await conn.commit()
            # Перезагружаем сделку
            d = await get_deal(deal_id)
    
    # Специальный формат для завершенных и отмененных сделок - обрабатываем в начале
    if d["status"] == DealStatus.RELEASED:
        # Проверяем доступ перед обработкой завершенной сделки
        allowed_ids = [d["owner_id"]]
        if d.get("partner_id"):
            allowed_ids.append(d["partner_id"])
        if d.get("buyer_id") and d.get("buyer_id") not in allowed_ids:
            allowed_ids.append(d["buyer_id"])
        
        if callback.from_user.id not in allowed_ids:
            await callback.answer("Нет доступа к этой сделке", show_alert=True)
            return
        
        # Определяем роль текущего пользователя
        role = get_role_name(callback.from_user.id, d)
        
        # Определяем контрагента и его роль
        partner_id = d.get("partner_id")
        if partner_id:
            partner = await get_user(partner_id)
            partner_stats = await get_user_statistics(partner_id) if partner else None
            
            # Определяем роль контрагента
            partner_role = "Покупатель" if role == "Продавец" else "Продавец"
            
            # Получаем сумму сделки
            if d.get("buy_amount"):
                amount_btc = Decimal(d['buy_amount'])
            else:
                amount_btc = Decimal(d['max_btc'])
            
            from utils import get_btc_rub_rate, btc_to_rub
            btc_rub_rate = await get_btc_rub_rate()
            amount_rub = btc_to_rub(amount_btc, btc_rub_rate)
            amount_rub_formatted = f"{amount_rub:,.0f}".replace(",", " ")
            amount_btc_formatted = f"{amount_btc:.8f}".rstrip('0').rstrip('.')
            
            # Формируем новый формат
            lines = [
                f"✨ <b>Сделка #{deal_id}</b> ✨",
                ""
            ]
            
            # Информация о контрагенте
            if partner:
                display_name = partner.get("display_name") or f"ID {partner['tg_id']}"
                lines.append(f"👤 <b>{partner_role}:</b> {display_name}")
                
                if partner_stats:
                    rating = partner_stats.get("average_rating", 0)
                    if rating > 0:
                        lines.append(f"⭐️ <b>Рейтинг:</b> {rating:.1f}/5.0")
                    else:
                        lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
                else:
                    lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            else:
                lines.append(f"👤 <b>{partner_role}:</b> Неизвестен")
                lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            
            lines.append(f"📊 <b>Статус:</b> Завершена")
            lines.append("")
            lines.append(f"💰 <b>Сумма:</b> {amount_btc_formatted} ({amount_rub_formatted} руб)")
            lines.append("")
            lines.append("🎉 <b>Сделка успешно завершена!</b>")
            
            # Используем новую функцию для генерации кнопок действий
            is_owner = callback.from_user.id == d["owner_id"]
            saved_payment_details = None
            
            if is_owner and d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
                from database import get_saved_payment_details
                saved_payment_details = await get_saved_payment_details(callback.from_user.id)
            
            kb = deal_actions_kb(d, callback.from_user.id, is_owner, saved_payment_details)
            
            text = "\n".join(lines)
            
            # Пытаемся отредактировать сообщение
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            
            await callback.answer()
            return
    
    # Специальный формат для отмененных сделок
    if d["status"] == DealStatus.CANCELLED:
        # Проверяем доступ перед обработкой отмененной сделки
        allowed_ids = [d["owner_id"]]
        if d.get("partner_id"):
            allowed_ids.append(d["partner_id"])
        if d.get("buyer_id") and d.get("buyer_id") not in allowed_ids:
            allowed_ids.append(d["buyer_id"])
        
        if callback.from_user.id not in allowed_ids:
            await callback.answer("Нет доступа к этой сделке", show_alert=True)
            return
        
        # Определяем роль текущего пользователя
        role = get_role_name(callback.from_user.id, d)
        
        # Определяем контрагента и его роль
        partner_id = d.get("partner_id")
        if partner_id:
            partner = await get_user(partner_id)
            partner_stats = await get_user_statistics(partner_id) if partner else None
            
            # Определяем роль контрагента
            partner_role = "Покупатель" if role == "Продавец" else "Продавец"
            
            # Получаем сумму сделки
            if d.get("buy_amount"):
                amount_btc = Decimal(d['buy_amount'])
            else:
                amount_btc = Decimal(d['max_btc'])
            
            from utils import get_btc_rub_rate, btc_to_rub, format_datetime
            btc_rub_rate = await get_btc_rub_rate()
            amount_rub = btc_to_rub(amount_btc, btc_rub_rate)
            amount_rub_formatted = f"{amount_rub:,.0f}".replace(",", " ")
            amount_btc_formatted = f"{amount_btc:.8f}".rstrip('0').rstrip('.')
            
            # Формируем новый формат
            lines = [
                f"✨ <b>Сделка #{deal_id}</b> ✨",
                ""
            ]
            
            # Информация о контрагенте
            if partner:
                display_name = partner.get("display_name") or f"ID {partner['tg_id']}"
                lines.append(f"👤 <b>{partner_role}:</b> {display_name}")
                
                if partner_stats:
                    rating = partner_stats.get("average_rating", 0)
                    if rating > 0:
                        lines.append(f"<b>Рейтинг:</b> {rating:.1f}/5.0")
                    else:
                        lines.append(f"<b>Рейтинг:</b> Нет оценок")
                else:
                    lines.append(f"<b>Рейтинг:</b> Нет оценок")
            else:
                lines.append(f"👤 <b>{partner_role}:</b> Неизвестен")
                lines.append(f"<b>Рейтинг:</b> Нет оценок")
            
            lines.append(f"📊 <b>Статус:</b> Отменена")
            lines.append("")
            lines.append(f"💰 <b>Сумма:</b> {amount_btc_formatted} ({amount_rub_formatted} руб)")
            lines.append("")
            lines.append(f"📅 <b>Создана:</b> {format_datetime(d['created_at'])}")
            
            # Используем новую функцию для генерации кнопок действий
            is_owner = callback.from_user.id == d["owner_id"]
            saved_payment_details = None
            
            if is_owner and d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
                from database import get_saved_payment_details
                saved_payment_details = await get_saved_payment_details(callback.from_user.id)
            
            kb = deal_actions_kb(d, callback.from_user.id, is_owner, saved_payment_details)
            
            text = "\n".join(lines)
            
            # Пытаемся отредактировать сообщение
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            
            await callback.answer()
            return
    
    # Проверяем доступ (включая buyer_id для совместимости)
    allowed_ids = [d["owner_id"]]
    if d.get("partner_id"):
        allowed_ids.append(d["partner_id"])
    if d.get("buyer_id") and d.get("buyer_id") not in allowed_ids:
        allowed_ids.append(d["buyer_id"])
    
    if callback.from_user.id not in allowed_ids:
        await callback.answer("Нет доступа к этой сделке", show_alert=True)
        return

    if (d.get("side") or "").upper() == "GARANT" and d.get("status") == DealStatus.MATCHED:
        from database import get_user
        from utils import btc_to_rub, get_btc_rub_rate
        other_id = d["partner_id"] if callback.from_user.id == d["owner_id"] else d["owner_id"]
        other = await get_user(other_id) if other_id else None
        other_name = None
        if other:
            other_name = f"@{other['username']}" if other.get("username") else (other.get("display_name") or f"ID {other['tg_id']}")
        amount_btc = Decimal(str(d.get("max_btc") or "0"))
        amount_str = f"{amount_btc:.8f}".rstrip("0").rstrip(".")
        rate = await get_btc_rub_rate()
        rub_str = f"{btc_to_rub(amount_btc, rate):,.0f}".replace(",", " ")
        text = (
            f"🛡 <b>Автогарант #{deal_id}</b>\n\n"
            f"✅ Депозит принят, сделка открыта.\n\n"
            f"💰 <b>{amount_str} BTC</b> (~{rub_str} руб)\n"
            f"👤 Участник: {other_name or '—'}"
        )
        is_owner = callback.from_user.id == d.get("owner_id")
        rows = []
        if is_owner:
            rows.append([InlineKeyboardButton(text="✅ Подтвердить получение фиата", callback_data=f"garant:fiat_ok:{deal_id}")])
            rows.append([InlineKeyboardButton(text="⚠️ Оспорить", callback_data=f"deal:dispute:{deal_id}")])
        rows.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
        rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:active")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        if is_owner:
            text += "\n\nКогда фиат будет у вас — подтвердите. BTC уйдёт второму участнику."
        else:
            text += "\n\nЖдём, пока инициатор подтвердит фиат. После этого BTC придёт на ваш кошелёк."
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        return
    
    # Формируем описание
    role = get_role_name(callback.from_user.id, d)
    status_emoji = get_status_emoji(d["status"])
    status_name = get_status_name(d["status"])
    
    # Импортируем необходимые функции для расчета
    from database import calculate_buyer_amounts, calculate_seller_amounts, get_user, get_user_statistics
    from utils import get_btc_rub_rate, btc_to_rub, format_rating_stars
    from config import PAYMENT_TIMEOUT_MINUTES
    
    # Рассчитываем суммы
    max_btc = Decimal(d.get("buy_amount") or d["max_btc"])
    buyer_amounts = calculate_buyer_amounts(max_btc)
    buyer_payment_btc = buyer_amounts['buyer_payment']  # НОВАЯ ЛОГИКА: базовая сумма без доплат
    
    # Получаем курс и рассчитываем сумму в рублях
    btc_rub_rate = await get_btc_rub_rate()
    buyer_payment_rub = btc_to_rub(buyer_payment_btc, btc_rub_rate)
    buyer_payment_rub_formatted = f"{buyer_payment_rub:,.0f}".replace(",", " ")
    
    role = get_role_name(callback.from_user.id, d)
    
    # Улучшенное отображение для активных сделок
    if d["status"] in [DealStatus.MATCHED, DealStatus.PAYMENT_PENDING]:
        # Импортируем константу для таймера
        from config import PAYMENT_TIMEOUT_MINUTES
        
        # Для покупателя - новый формат
        if role == "Покупатель":
            # Получаем информацию о контрагенте (продавце)
            seller_id = d["owner_id"] if d["side"] == "SELL" else d.get("partner_id")
            seller = None
            seller_stats = None
            if seller_id:
                from database import get_user, get_user_statistics
                from utils import format_rating_stars
                seller = await get_user(seller_id)
                seller_stats = await get_user_statistics(seller_id) if seller else None
            
            # НОВАЯ ЛОГИКА: Покупатель платит базовую сумму, получает 100% (комиссия 0% временно)
            buyer_receive_btc = buyer_amounts['buyer_receive']  # max_btc * 1.0 (100% при 0% комиссии)
            
            # Формируем новый формат
            lines = [
                f"⌛️ <b>Статус сделки: Ожидает вашей оплаты</b>",
                ""
            ]
            
            # Информация о контрагенте
            if seller:
                display_name = seller.get("display_name") or f"ID {seller['tg_id']}"
                lines.append(f"👤 <b>Контрагент:</b> {display_name}")
                
                if seller_stats:
                    rating = seller_stats.get("average_rating", 0)
                    if rating > 0:
                        lines.append(f"⭐️ <b>Рейтинг:</b> {rating:.1f}/5.0")
                    else:
                        lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
                else:
                    lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            else:
                lines.append(f"👤 <b>Контрагент:</b> Неизвестен")
                lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            
            lines.append("")
            
            # Время на оплату
            # В статусах MATCHED и PAYMENT_PENDING matched_at должен быть установлен
            # когда покупатель подтверждает начало сделки (в deal_confirm_start)
            matched_at_str = d.get("matched_at")
            if matched_at_str:
                try:
                    matched_at = datetime.fromisoformat(matched_at_str)
                    payment_deadline = matched_at + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
                    now = datetime.utcnow()
                    time_remaining = payment_deadline - now
                    
                    if time_remaining.total_seconds() > 0:
                        minutes_left = int(time_remaining.total_seconds() // 60)
                        seconds_left = int(time_remaining.total_seconds() % 60)
                        # Формат MM:SS для таймера
                        time_left_str = f"{minutes_left:02d}:{seconds_left:02d}"
                        lines.append(f"⏰ <b>Время на оплату:</b> {time_left_str}")
                    else:
                        # Время истекло - сделка должна перейти в спор
                        lines.append(f"⏰ <b>Время на оплату:</b> 00:00")
                except (ValueError, TypeError, AttributeError) as e:
                    # Если ошибка парсинга времени, показываем, что таймер не установлен
                    # Логируем для отладки
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Ошибка парсинга matched_at для сделки {deal_id}: {e}, matched_at_str={matched_at_str}")
                    lines.append(f"⏰ <b>Время на оплату:</b> Ошибка расчета времени")
            else:
                # Если matched_at нет, это не должно происходить в MATCHED/PAYMENT_PENDING
                # Но на всякий случай показываем сообщение
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"matched_at не установлен для сделки {deal_id} со статусом {d.get('status')}")
                lines.append(f"⏰ <b>Время на оплату:</b> Таймер еще не активирован")
            
            lines.append("")
            
            # Сумма к оплате (жирным, только в рублях)
            lines.append(f"💰 <b>Сумма к оплате:</b> {buyer_payment_rub_formatted} руб")
            lines.append("")
            
            # Что получит покупатель
            lines.append(f"💸 <b>Вы получите:</b> {buyer_receive_btc:.8f} BTC")
            lines.append("")
            
            # Реквизиты продавца (жирным)
            if d.get("payment_details"):
                lines.append(f"💳 <b>Реквизиты продавца для оплаты:</b>")
                lines.append("")
                lines.append(f"<b>{d['payment_details']}</b>")
                lines.append("")
            
            # Предупреждение (всё жирным)
            lines.append(f"❗️ <b>Сверяйте реквизиты, переводите указанную сумму рубль в рубль</b>")
        
        else:
            # Для продавца - новый формат
            # Получаем информацию о покупателе
            buyer_id = d.get("partner_id") if d["side"] == "SELL" else d.get("owner_id")
            buyer = None
            buyer_stats = None
            if buyer_id:
                buyer = await get_user(buyer_id)
                buyer_stats = await get_user_statistics(buyer_id) if buyer else None
            
            # Формируем новый формат для продавца
            lines = [
                f"⌛️ <b>Статус сделки: Ожидает оплаты от покупателя</b>",
                ""
            ]
            
            # Информация о покупателе
            if buyer:
                display_name = buyer.get("display_name") or f"ID {buyer['tg_id']}"
                lines.append(f"👤 <b>Покупатель:</b> {display_name}")
                
                if buyer_stats:
                    rating = buyer_stats.get("average_rating", 0)
                    if rating > 0:
                        lines.append(f"⭐️ <b>Рейтинг:</b> {rating:.1f}/5.0")
                    else:
                        lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
                else:
                    lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            else:
                lines.append(f"👤 <b>Покупатель:</b> Неизвестен")
                lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            
            lines.append(f"📅 <b>Создана:</b> {format_datetime(d['created_at'])}")
            lines.append("")
            
            # Время на оплату покупателем
            matched_at_str = d.get("matched_at")
            if matched_at_str:
                try:
                    matched_at = datetime.fromisoformat(matched_at_str)
                    payment_deadline = matched_at + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
                    now = datetime.utcnow()
                    time_remaining = payment_deadline - now
                    
                    if time_remaining.total_seconds() > 0:
                        minutes_left = int(time_remaining.total_seconds() // 60)
                        seconds_left = int(time_remaining.total_seconds() % 60)
                        # Формат MM:SS для таймера
                        time_left_str = f"{minutes_left:02d}:{seconds_left:02d}"
                        lines.append(f"⏰ <b>Время на оплату покупателем:</b> {time_left_str}")
                    else:
                        lines.append(f"⏰ <b>Время на оплату покупателем:</b> 00:00")
                except (ValueError, TypeError, AttributeError) as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Ошибка парсинга matched_at для продавца в сделке {deal_id}: {e}")
                    lines.append(f"⏰ <b>Время на оплату покупателем:</b> Ошибка расчета")
            else:
                # Если matched_at нет, устанавливаем его
                matched_at_time = datetime.utcnow()
                async with db() as conn:
                    await conn.execute(
                        "UPDATE deals SET matched_at=? WHERE id=?",
                        (matched_at_time.isoformat(), deal_id)
                    )
                    await conn.commit()
                d = await get_deal(deal_id)
                # Пересчитываем время
                payment_deadline = matched_at_time + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
                now = datetime.utcnow()
                time_remaining = payment_deadline - now
                if time_remaining.total_seconds() > 0:
                    minutes_left = int(time_remaining.total_seconds() // 60)
                    seconds_left = int(time_remaining.total_seconds() % 60)
                    # Формат MM:SS для таймера
                    time_left_str = f"{minutes_left:02d}:{seconds_left:02d}"
                    lines.append(f"⏰ <b>Время на оплату покупателем:</b> {time_left_str}")
                else:
                    lines.append(f"⏰ <b>Время на оплату покупателем:</b> 00:00")
            
            lines.append("")
            
            # Сумма сделки
            lines.append(f"💰 <b>Сумма сделки:</b>")
            lines.append(f"   {max_btc:.8f} BTC")
            lines.append(f"   ({buyer_payment_rub_formatted} руб)")
            lines.append("")
            
            # Продавец получает то, что платит покупатель (max_btc * 1.025)
            seller_receive_rub = buyer_payment_rub  # Продавец получает сумму, которую платит покупатель
            seller_receive_rub_formatted = f"{seller_receive_rub:,.0f}".replace(",", " ")
            
            lines.append(f"💸 <b>Вы получите:</b>")
            lines.append(f"   {seller_receive_rub_formatted} руб")
            lines.append("")
    
    else:
        # Для статуса ACTIVE - новый упрощенный формат
        if d["status"] == DealStatus.ACTIVE:
            from utils import format_datetime_short
            # Определяем сумму сделки
            if d.get("buy_amount"):
                amount_btc = Decimal(d['buy_amount'])
            else:
                # Если min == max, показываем одну сумму, иначе диапазон
                min_btc = Decimal(d['min_btc'])
                max_btc_decimal = Decimal(d['max_btc'])
                if min_btc == max_btc_decimal:
                    amount_btc = max_btc_decimal
                else:
                    # Для диапазона используем максимальное значение
                    amount_btc = max_btc_decimal
            
            amount_rub = btc_to_rub(amount_btc, btc_rub_rate)
            amount_rub_formatted = f"{amount_rub:,.0f}".replace(",", " ")
            amount_btc_formatted = f"{amount_btc:.8f}".rstrip('0').rstrip('.')
            
            lines = [
                f"✨ {status_emoji} <b>Сделка #{deal_id}</b> ✨",
                "",
                f"⏳ <b>Статус сделки:</b> Ожидает контрагента",
                "",
                f"💰 <b>Сумма сделки:</b> {amount_btc_formatted} BTC ({amount_rub_formatted} руб)",
                "",
                f"🚀 <b>Заявка создана:</b> {format_datetime_short(d['created_at'])}"
            ]
        else:
            # Для других статусов - стандартный формат
            lines = [
                f"✨ {status_emoji} <b>Сделка #{deal_id}</b> ✨",
                "",
                f"👤 <b>Ваша роль:</b> {role}",
                f"📊 <b>Статус:</b> {status_name}",
                f"📋 <b>Тип:</b> {'Автогарант' if (d.get('side') or '').upper()=='GARANT' else ('Покупка' if d['side']=='BUY' else 'Продажа')}",
                ""
            ]
            
            # Информация о сумме с рублями
            if d.get("buy_amount"):
                amount_btc = Decimal(d['buy_amount'])
                amount_rub = btc_to_rub(amount_btc, btc_rub_rate)
                amount_rub_formatted = f"{amount_rub:,.0f}".replace(",", " ")
                lines.append(f"💰 <b>Сумма:</b> {d['buy_amount']} BTC ({amount_rub_formatted} руб)")
            else:
                min_btc = Decimal(d['min_btc'])
                max_btc_decimal = Decimal(d['max_btc'])
                min_rub = btc_to_rub(min_btc, btc_rub_rate)
                max_rub = btc_to_rub(max_btc_decimal, btc_rub_rate)
                min_rub_formatted = f"{min_rub:,.0f}".replace(",", " ")
                max_rub_formatted = f"{max_rub:,.0f}".replace(",", " ")
                lines.append(f"💰 <b>Лимиты:</b> {d['min_btc']} — {d['max_btc']} BTC ({min_rub_formatted} — {max_rub_formatted} руб)")
            
            # Информация о контрагенте
            partner_id = d.get("partner_id")
            if partner_id:
                partner = await get_user(partner_id)
                partner_stats = await get_user_statistics(partner_id) if partner else None
                
                if partner:
                    display_name = partner.get("display_name")
                    if display_name:
                        lines.append(f"\n👤 <b>Контрагент:</b> {display_name}")
                    else:
                        lines.append(f"\n👤 <b>Контрагент:</b> ID {partner['tg_id']}")
                    
                    if partner_stats:
                        rating = partner_stats.get("average_rating", 0)
                        if rating > 0:
                            stars = format_rating_stars(rating)
                            lines.append(f"⭐️ <b>Рейтинг:</b> {rating:.1f}/5.0 {stars}")
            
            # Используем глобальный format_datetime из импорта в начале файла
            # (не локальный, чтобы избежать UnboundLocalError из-за локального импорта format_datetime_short выше)
            import utils
            lines.append(f"\n📅 <b>Создана:</b> {utils.format_datetime(d['created_at'])}")
        
        # Используем глобальный format_datetime для всех статусов
        import utils as utils_module
        if d.get("matched_at"):
            lines.append(f"🤝 <b>Найден контрагент:</b> {utils_module.format_datetime(d['matched_at'])}")
        
        if d.get("completed_at"):
            lines.append(f"✅ <b>Завершена:</b> {utils_module.format_datetime(d['completed_at'])}")
        
        lines.append("")
        
        # Дополнительная информация
        if d["status"] == DealStatus.AWAITING_DEPOSIT and callback.from_user.id == d["owner_id"]:
            lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"\n💰 <b>Отправьте {d['max_btc']} BTC на адрес:</b>")
            lines.append(f"<code>{d['escrow_address']}</code>")
            lines.append(f"")
        
        # Информация о необходимости ввода реквизитов
        if (
            d["status"] in [DealStatus.ESCROWED, DealStatus.CONFIRMING]
            and callback.from_user.id == d["owner_id"]
            and (d.get("side") or "").upper() != "GARANT"
        ):
            lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            if not d.get("payment_details"):
                lines.append(f"\n✅ <b>Депозит подтверждён!</b>")
                lines.append(f"⚠️ <b>Необходимо ввести реквизиты для получения оплаты.</b>")
            else:
                lines.append(f"\n✅ <b>Депозит подтверждён!</b>")
                lines.append(f"💳 <b>Реквизиты указаны</b>")
            lines.append(f"")
    
    if d["status"] == DealStatus.DISPUTED:
        lines.append("\n⚠️ <b>По сделке открыт спор</b>\n<b>Ожидайте администратора.</b>")
    
    # Используем новую функцию для генерации кнопок действий
    is_owner = callback.from_user.id == d["owner_id"]
    saved_payment_details = None
    
    if is_owner and d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        from database import get_saved_payment_details
        saved_payment_details = await get_saved_payment_details(callback.from_user.id)
    
    kb = deal_actions_kb(d, callback.from_user.id, is_owner, saved_payment_details)
    
    text = "\n".join(lines)
    
    # Пытаемся отредактировать сообщение
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        # Если редактирование не удалось, отправляем новое сообщение
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    
    await callback.answer()


# ==================== МЕНЮ ДЕЙСТВИЙ ДЛЯ СДЕЛКИ ====================

@router.callback_query(F.data.startswith("deal:actions:"))
async def deal_actions_menu(callback: CallbackQuery):
    """Меню действий для сделки"""
    await callback.answer()  # Сразу убираем индикатор загрузки
    
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.message.answer("❌ Неверный формат команды")
            return
        
        deal_id = int(parts[2])
    except (ValueError, IndexError, AttributeError):
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("❌ Сделка не найдена")
        return
    
    # Проверяем доступ
    if callback.from_user.id != d["owner_id"]:
        await callback.message.answer("❌ Нет доступа")
        return
    
    is_owner = True
    from database import get_saved_payment_details
    saved_payment_details = await get_saved_payment_details(callback.from_user.id)
    
    buttons = []
    
    # Добавляем действия в зависимости от статуса
    if d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        # Изменение реквизитов
        buttons.append([InlineKeyboardButton(text="✏️ Изменить реквизиты", callback_data=f"deal:actions:payment:{deal_id}")])
        
        # Информация о сделке
        buttons.append([InlineKeyboardButton(text="ℹ️ Информация о сделке", callback_data=f"deal:info:{deal_id}")])
        
        # Просмотр текущих реквизитов
        if d.get("payment_details"):
            buttons.append([InlineKeyboardButton(text="👁️ Просмотр реквизитов", callback_data=f"deal:view_payment:{deal_id}")])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад к сделке", callback_data=f"mydeal:view:{deal_id}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    text = (
        f"⚙️ <b>Действия для сделки #{deal_id}</b> ⚙️\n\n"
        f"📊 <b>Статус:</b> {get_status_name(d['status'])}\n\n"
        f"💡 <b>Выберите действие:</b>"
    )
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("deal:actions:payment:"))
async def deal_actions_payment(callback: CallbackQuery, state: FSMContext):
    """Меню выбора/изменения реквизитов"""
    await callback.answer()  # Сразу убираем индикатор загрузки
    
    try:
        parts = callback.data.split(":")
        if len(parts) < 4:
            await callback.message.answer("❌ Неверный формат команды")
            return
        
        deal_id = int(parts[3])
    except (ValueError, IndexError, AttributeError):
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("❌ Сделка не найдена")
        return
    
    # Проверяем доступ
    if callback.from_user.id != d["owner_id"]:
        await callback.message.answer("❌ Нет доступа")
        return
    
    # Проверяем статус
    if d["status"] not in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        await callback.message.answer("❌ Нельзя изменить реквизиты для этой сделки")
        return
    
        from database import get_saved_payment_details
    saved_payment_details = await get_saved_payment_details(callback.from_user.id)
    
    buttons = []
    
    if saved_payment_details:
        buttons.append([InlineKeyboardButton(text="✏️ Ввести новые реквизиты", callback_data=f"sell:enter_new_payment:{deal_id}")])
        for detail in saved_payment_details:
            buttons.append([InlineKeyboardButton(text=f"📋 {detail['title']}", callback_data=f"sell:use_payment:{deal_id}:{detail['id']}")])
        else:
            buttons.append([InlineKeyboardButton(text="✏️ Ввести реквизиты", callback_data=f"sell:enter_new_payment:{deal_id}")])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:actions:{deal_id}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    current_payment_details = d.get("payment_details")
    
    if current_payment_details:
        text = (
            f"💳 <b>Изменение реквизитов для сделки #{deal_id}</b> 💳\n\n"
            f"📋 <b>Текущие реквизиты:</b>\n"
            f"<code>{current_payment_details}</code>\n\n"
            f"💡 <b>Выберите новые реквизиты:</b>"
        )
    else:
        text = (
            f"💳 <b>Указание реквизитов для сделки #{deal_id}</b> 💳\n\n"
            f"💡 <b>Выберите реквизиты для получения оплаты:</b>"
        )
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("deal:view_payment:"))
async def deal_view_payment(callback: CallbackQuery):
    """Просмотр текущих реквизитов"""
    await callback.answer()  # Сразу убираем индикатор загрузки
    
    try:
        deal_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError, AttributeError):
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("❌ Сделка не найдена")
        return
    
    # Проверяем доступ
    if callback.from_user.id != d["owner_id"]:
        await callback.message.answer("❌ Нет доступа")
        return
    
    payment_details = d.get("payment_details")
    
    if not payment_details:
        await callback.message.answer("❌ Реквизиты не указаны")
        return
    
    text = (
        f"💳 <b>Реквизиты для сделки #{deal_id}</b> 💳\n\n"
        f"📋 <b>Текущие реквизиты:</b>\n"
        f"<code>{payment_details}</code>\n\n"
        f"💡 <b>Чтобы изменить реквизиты, вернитесь в меню действий.</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"deal:actions:payment:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:actions:{deal_id}")]
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("deal:info:"))
async def deal_info(callback: CallbackQuery):
    """Информация о сделке"""
    await callback.answer()  # Сразу убираем индикатор загрузки
    
    try:
        deal_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError, AttributeError):
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("❌ Сделка не найдена")
        return
    
    # Проверяем доступ
    if callback.from_user.id not in [d["owner_id"], d.get("partner_id")]:
        await callback.message.answer("❌ Нет доступа")
        return
    
    role = get_role_name(callback.from_user.id, d)
    status_emoji = get_status_emoji(d["status"])
    status_name = get_status_name(d["status"])
    
    lines = [
        f"ℹ️ <b>Информация о сделке #{deal_id}</b> ℹ️",
        "",
        f"👤 <b>Ваша роль:</b> {role}",
        f"📊 <b>Статус:</b> {status_emoji} {status_name}",
        f"📋 <b>Тип:</b> {'Автогарант' if (d.get('side') or '').upper()=='GARANT' else ('Покупка' if d['side']=='BUY' else 'Продажа')}",
        "",
    ]
    
    # Информация о сумме
    if d.get("buy_amount"):
        lines.append(f"💰 <b>Сумма сделки:</b> {d['buy_amount']} BTC")
    else:
        lines.append(f"💰 <b>Лимиты:</b> {d['min_btc']} — {d['max_btc']} BTC")
    
    lines.append(f"📅 <b>Создана:</b> {format_datetime(d['created_at'])}")
    
    if d.get("matched_at"):
        lines.append(f"🤝 <b>Найден контрагент:</b> {format_datetime(d['matched_at'])}")
    
    if d.get("payment_marked_at"):
        lines.append(f"💳 <b>Оплата отмечена:</b> {format_datetime(d['payment_marked_at'])}")
    
    if d.get("seller_confirmed_at"):
        lines.append(f"✅ <b>Подтверждено продавцом:</b> {format_datetime(d['seller_confirmed_at'])}")
    
    if d.get("completed_at"):
        lines.append(f"🎉 <b>Завершена:</b> {format_datetime(d['completed_at'])}")
    
    lines.append("")
    
    # Информация о реквизитах (только для продавца)
    if d.get("payment_details") and role == "Продавец":
        lines.append(f"💳 <b>Ваши реквизиты:</b>\n<code>{d['payment_details']}</code>")
        lines.append("")
    
    # Кошелек покупателя убран - не нужен продавцу
    
    # Эскроу адрес
    if d.get("escrow_address") and role == "Продавец":
        lines.append(f"🔗 <b>Эскроу адрес:</b>\n<code>{d['escrow_address']}</code>")
        lines.append("")
    
    # Кнопка назад
    is_owner = callback.from_user.id == d["owner_id"]
    if is_owner and d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        back_to = f"deal:actions:{deal_id}"
    else:
        back_to = f"mydeal:view:{deal_id}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_to)]
    ])
    
    text = "\n".join(lines)
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    
    # Улучшенное отображение для активных сделок
    if d["status"] in [DealStatus.MATCHED, DealStatus.PAYMENT_PENDING]:
        # Импортируем константу для таймера
        from config import PAYMENT_TIMEOUT_MINUTES
        
        # Для покупателя - новый формат
        if role == "Покупатель":
            # Получаем информацию о контрагенте (продавце)
            seller_id = d["owner_id"] if d["side"] == "SELL" else d.get("partner_id")
            seller = None
            seller_stats = None
            if seller_id:
                from database import get_user, get_user_statistics
                from utils import format_rating_stars
                seller = await get_user(seller_id)
                seller_stats = await get_user_statistics(seller_id) if seller else None
            
            # НОВАЯ ЛОГИКА: Покупатель платит базовую сумму, получает 100% (комиссия 0% временно)
            buyer_receive_btc = buyer_amounts['buyer_receive']  # max_btc * 1.0 (100% при 0% комиссии)
            
            # Формируем новый формат
            lines = [
                f"⌛️ <b>Статус сделки: Ожидает вашей оплаты</b>",
                ""
            ]
            
            # Информация о контрагенте
            if seller:
                display_name = seller.get("display_name") or f"ID {seller['tg_id']}"
                lines.append(f"👤 <b>Контрагент:</b> {display_name}")
                
                if seller_stats:
                    rating = seller_stats.get("average_rating", 0)
                    if rating > 0:
                        lines.append(f"⭐️ <b>Рейтинг:</b> {rating:.1f}/5.0")
                    else:
                        lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
                else:
                    lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            else:
                lines.append(f"👤 <b>Контрагент:</b> Неизвестен")
                lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            
            lines.append("")
            
            # Время на оплату
            # В статусах MATCHED и PAYMENT_PENDING matched_at должен быть установлен
            # когда покупатель подтверждает начало сделки (в deal_confirm_start)
            matched_at_str = d.get("matched_at")
            if matched_at_str:
                try:
                    matched_at = datetime.fromisoformat(matched_at_str)
                    payment_deadline = matched_at + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
                    now = datetime.utcnow()
                    time_remaining = payment_deadline - now
                    
                    if time_remaining.total_seconds() > 0:
                        minutes_left = int(time_remaining.total_seconds() // 60)
                        seconds_left = int(time_remaining.total_seconds() % 60)
                        # Формат MM:SS для таймера
                        time_left_str = f"{minutes_left:02d}:{seconds_left:02d}"
                        lines.append(f"⏰ <b>Время на оплату:</b> {time_left_str}")
                    else:
                        # Время истекло - сделка должна перейти в спор
                        lines.append(f"⏰ <b>Время на оплату:</b> 00:00")
                except (ValueError, TypeError, AttributeError) as e:
                    # Если ошибка парсинга времени, показываем, что таймер не установлен
                    # Логируем для отладки
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Ошибка парсинга matched_at для сделки {deal_id}: {e}, matched_at_str={matched_at_str}")
                    lines.append(f"⏰ <b>Время на оплату:</b> Ошибка расчета времени")
            else:
                # Если matched_at нет, это не должно происходить в MATCHED/PAYMENT_PENDING
                # Но на всякий случай показываем сообщение
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"matched_at не установлен для сделки {deal_id} со статусом {d.get('status')}")
                lines.append(f"⏰ <b>Время на оплату:</b> Таймер еще не активирован")
            
            lines.append("")
            
            # Сумма к оплате (жирным, только в рублях)
            lines.append(f"💰 <b>Сумма к оплате:</b> {buyer_payment_rub_formatted} руб")
            lines.append("")
            
            # Что получит покупатель
            lines.append(f"💸 <b>Вы получите:</b> {buyer_receive_btc:.8f} BTC")
            lines.append("")
            
            # Реквизиты продавца (жирным)
            if d.get("payment_details"):
                lines.append(f"💳 <b>Реквизиты продавца для оплаты:</b>")
                lines.append("")
                lines.append(f"<b>{d['payment_details']}</b>")
                lines.append("")
            
            # Предупреждение (всё жирным)
            lines.append(f"❗️ <b>Сверяйте реквизиты, переводите указанную сумму рубль в рубль</b>")
        
        else:
            # Для продавца - новый формат
            # Получаем информацию о покупателе
            buyer_id = d.get("partner_id") if d["side"] == "SELL" else d.get("owner_id")
            buyer = None
            buyer_stats = None
            if buyer_id:
                buyer = await get_user(buyer_id)
                buyer_stats = await get_user_statistics(buyer_id) if buyer else None
            
            # Формируем новый формат для продавца
            lines = [
                f"⌛️ <b>Статус сделки: Ожидает оплаты от покупателя</b>",
                ""
            ]
            
            # Информация о покупателе
            if buyer:
                display_name = buyer.get("display_name") or f"ID {buyer['tg_id']}"
                lines.append(f"👤 <b>Покупатель:</b> {display_name}")
                
                if buyer_stats:
                    rating = buyer_stats.get("average_rating", 0)
                    if rating > 0:
                        lines.append(f"⭐️ <b>Рейтинг:</b> {rating:.1f}/5.0")
                    else:
                        lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
                else:
                    lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            else:
                lines.append(f"👤 <b>Покупатель:</b> Неизвестен")
                lines.append(f"⭐️ <b>Рейтинг:</b> Нет оценок")
            
            lines.append(f"📅 <b>Создана:</b> {format_datetime(d['created_at'])}")
            lines.append("")
            
            # Время на оплату покупателем
            matched_at_str = d.get("matched_at")
            if matched_at_str:
                try:
                    matched_at = datetime.fromisoformat(matched_at_str)
                    payment_deadline = matched_at + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
                    now = datetime.utcnow()
                    time_remaining = payment_deadline - now
                    
                    if time_remaining.total_seconds() > 0:
                        minutes_left = int(time_remaining.total_seconds() // 60)
                        seconds_left = int(time_remaining.total_seconds() % 60)
                        # Формат MM:SS для таймера
                        time_left_str = f"{minutes_left:02d}:{seconds_left:02d}"
                        lines.append(f"⏰ <b>Время на оплату покупателем:</b> {time_left_str}")
                    else:
                        lines.append(f"⏰ <b>Время на оплату покупателем:</b> 00:00")
                except (ValueError, TypeError, AttributeError) as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Ошибка парсинга matched_at для продавца в сделке {deal_id}: {e}")
                    lines.append(f"⏰ <b>Время на оплату покупателем:</b> Ошибка расчета")
            else:
                # Если matched_at нет, устанавливаем его
                matched_at_time = datetime.utcnow()
                async with db() as conn:
                    await conn.execute(
                        "UPDATE deals SET matched_at=? WHERE id=?",
                        (matched_at_time.isoformat(), deal_id)
                    )
                    await conn.commit()
                d = await get_deal(deal_id)
                # Пересчитываем время
                payment_deadline = matched_at_time + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
                now = datetime.utcnow()
                time_remaining = payment_deadline - now
                if time_remaining.total_seconds() > 0:
                    minutes_left = int(time_remaining.total_seconds() // 60)
                    seconds_left = int(time_remaining.total_seconds() % 60)
                    # Формат MM:SS для таймера
                    time_left_str = f"{minutes_left:02d}:{seconds_left:02d}"
                    lines.append(f"⏰ <b>Время на оплату покупателем:</b> {time_left_str}")
                else:
                    lines.append(f"⏰ <b>Время на оплату покупателем:</b> 00:00")
            
            lines.append("")
            
            # Сумма сделки
            lines.append(f"💰 <b>Сумма сделки:</b>")
            lines.append(f"   {max_btc:.8f} BTC")
            lines.append(f"   ({buyer_payment_rub_formatted} руб)")
            lines.append("")
            
            # Продавец получает то, что платит покупатель (max_btc * 1.025)
            seller_receive_rub = buyer_payment_rub  # Продавец получает сумму, которую платит покупатель
            seller_receive_rub_formatted = f"{seller_receive_rub:,.0f}".replace(",", " ")
            
            lines.append(f"💸 <b>Вы получите:</b>")
            lines.append(f"   {seller_receive_rub_formatted} руб")
            lines.append("")
    
    else:
        # Для статуса ACTIVE - новый упрощенный формат
        if d["status"] == DealStatus.ACTIVE:
            from utils import format_datetime_short
            # Определяем сумму сделки
            if d.get("buy_amount"):
                amount_btc = Decimal(d['buy_amount'])
            else:
                # Если min == max, показываем одну сумму, иначе диапазон
                min_btc = Decimal(d['min_btc'])
                max_btc_decimal = Decimal(d['max_btc'])
                if min_btc == max_btc_decimal:
                    amount_btc = max_btc_decimal
                else:
                    # Для диапазона используем максимальное значение
                    amount_btc = max_btc_decimal
            
            amount_rub = btc_to_rub(amount_btc, btc_rub_rate)
            amount_rub_formatted = f"{amount_rub:,.0f}".replace(",", " ")
            amount_btc_formatted = f"{amount_btc:.8f}".rstrip('0').rstrip('.')
            
            lines = [
                f"✨ {status_emoji} <b>Сделка #{deal_id}</b> ✨",
                "",
                f"⏳ <b>Статус сделки:</b> Ожидает контрагента",
                "",
                f"💰 <b>Сумма сделки:</b> {amount_btc_formatted} BTC ({amount_rub_formatted} руб)",
                "",
                f"🚀 <b>Заявка создана:</b> {format_datetime_short(d['created_at'])}"
            ]
        else:
            # Для других статусов - стандартный формат
            lines = [
                f"✨ {status_emoji} <b>Сделка #{deal_id}</b> ✨",
                "",
                f"👤 <b>Ваша роль:</b> {role}",
                f"📊 <b>Статус:</b> {status_name}",
                f"📋 <b>Тип:</b> {'Автогарант' if (d.get('side') or '').upper()=='GARANT' else ('Покупка' if d['side']=='BUY' else 'Продажа')}",
                ""
            ]
            
            # Информация о сумме с рублями
            if d.get("buy_amount"):
                amount_btc = Decimal(d['buy_amount'])
                amount_rub = btc_to_rub(amount_btc, btc_rub_rate)
                amount_rub_formatted = f"{amount_rub:,.0f}".replace(",", " ")
                lines.append(f"💰 <b>Сумма:</b> {d['buy_amount']} BTC ({amount_rub_formatted} руб)")
            else:
                min_btc = Decimal(d['min_btc'])
                max_btc_decimal = Decimal(d['max_btc'])
                min_rub = btc_to_rub(min_btc, btc_rub_rate)
                max_rub = btc_to_rub(max_btc_decimal, btc_rub_rate)
                min_rub_formatted = f"{min_rub:,.0f}".replace(",", " ")
                max_rub_formatted = f"{max_rub:,.0f}".replace(",", " ")
                lines.append(f"💰 <b>Лимиты:</b> {d['min_btc']} — {d['max_btc']} BTC ({min_rub_formatted} — {max_rub_formatted} руб)")
            
            # Информация о контрагенте
            partner_id = d.get("partner_id")
            if partner_id:
                partner = await get_user(partner_id)
                partner_stats = await get_user_statistics(partner_id) if partner else None
                
                if partner:
                    display_name = partner.get("display_name")
                    if display_name:
                        lines.append(f"\n👤 <b>Контрагент:</b> {display_name}")
                    else:
                        lines.append(f"\n👤 <b>Контрагент:</b> ID {partner['tg_id']}")
                    
                    if partner_stats:
                        rating = partner_stats.get("average_rating", 0)
                        if rating > 0:
                            stars = format_rating_stars(rating)
                            lines.append(f"⭐️ <b>Рейтинг:</b> {rating:.1f}/5.0 {stars}")
            
            # Используем глобальный format_datetime из импорта в начале файла
            # (не локальный, чтобы избежать UnboundLocalError из-за локального импорта format_datetime_short выше)
            import utils
            lines.append(f"\n📅 <b>Создана:</b> {utils.format_datetime(d['created_at'])}")
        
        # Используем глобальный format_datetime для всех статусов
        import utils as utils_module
        if d.get("matched_at"):
            lines.append(f"🤝 <b>Найден контрагент:</b> {utils_module.format_datetime(d['matched_at'])}")
        
        if d.get("completed_at"):
            lines.append(f"✅ <b>Завершена:</b> {utils_module.format_datetime(d['completed_at'])}")
        
        lines.append("")
        
        # Дополнительная информация
        if d["status"] == DealStatus.AWAITING_DEPOSIT and callback.from_user.id == d["owner_id"]:
            lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"\n💰 <b>Отправьте {d['max_btc']} BTC на адрес:</b>")
            lines.append(f"<code>{d['escrow_address']}</code>")
            lines.append(f"")
        
        # Информация о необходимости ввода реквизитов
        if (
            d["status"] in [DealStatus.ESCROWED, DealStatus.CONFIRMING]
            and callback.from_user.id == d["owner_id"]
            and (d.get("side") or "").upper() != "GARANT"
        ):
            lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            if not d.get("payment_details"):
                lines.append(f"\n✅ <b>Депозит подтверждён!</b>")
                lines.append(f"⚠️ <b>Необходимо ввести реквизиты для получения оплаты.</b>")
            else:
                lines.append(f"\n✅ <b>Депозит подтверждён!</b>")
                lines.append(f"💳 <b>Реквизиты указаны</b>")
            lines.append(f"")
    
    if d["status"] == DealStatus.DISPUTED:
        lines.append("\n⚠️ <b>По сделке открыт спор</b>\n<b>Ожидайте администратора.</b>")
    
    # Используем новую функцию для генерации кнопок действий
    is_owner = callback.from_user.id == d["owner_id"]
    saved_payment_details = None
    
    if is_owner and d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        from database import get_saved_payment_details
        saved_payment_details = await get_saved_payment_details(callback.from_user.id)
    
    kb = deal_actions_kb(d, callback.from_user.id, is_owner, saved_payment_details)
    
    text = "\n".join(lines)
    
    # Пытаемся отредактировать сообщение
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        # Если редактирование не удалось, отправляем новое сообщение
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    
    await callback.answer()


# ==================== МЕНЮ ДЕЙСТВИЙ ДЛЯ СДЕЛКИ ====================

@router.callback_query(F.data.startswith("deal:actions:"))
async def deal_actions_menu(callback: CallbackQuery):
    """Меню действий для сделки"""
    await callback.answer()  # Сразу убираем индикатор загрузки
    
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.message.answer("❌ Неверный формат команды")
            return
        
        deal_id = int(parts[2])
    except (ValueError, IndexError, AttributeError):
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("❌ Сделка не найдена")
        return
    
    # Проверяем доступ
    if callback.from_user.id != d["owner_id"]:
        await callback.message.answer("❌ Нет доступа")
        return
    
    is_owner = True
    from database import get_saved_payment_details
    saved_payment_details = await get_saved_payment_details(callback.from_user.id)
    
    buttons = []
    
    # Добавляем действия в зависимости от статуса
    if d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        # Изменение реквизитов
        buttons.append([InlineKeyboardButton(text="✏️ Изменить реквизиты", callback_data=f"deal:actions:payment:{deal_id}")])
        
        # Информация о сделке
        buttons.append([InlineKeyboardButton(text="ℹ️ Информация о сделке", callback_data=f"deal:info:{deal_id}")])
        
        # Просмотр текущих реквизитов
        if d.get("payment_details"):
            buttons.append([InlineKeyboardButton(text="👁️ Просмотр реквизитов", callback_data=f"deal:view_payment:{deal_id}")])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад к сделке", callback_data=f"mydeal:view:{deal_id}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    text = (
        f"⚙️ <b>Действия для сделки #{deal_id}</b> ⚙️\n\n"
        f"📊 <b>Статус:</b> {get_status_name(d['status'])}\n\n"
        f"💡 <b>Выберите действие:</b>"
    )
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("deal:actions:payment:"))
async def deal_actions_payment(callback: CallbackQuery, state: FSMContext):
    """Меню выбора/изменения реквизитов"""
    await callback.answer()  # Сразу убираем индикатор загрузки
    
    try:
        parts = callback.data.split(":")
        if len(parts) < 4:
            await callback.message.answer("❌ Неверный формат команды")
            return
        
        deal_id = int(parts[3])
    except (ValueError, IndexError, AttributeError):
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("❌ Сделка не найдена")
        return
    
    # Проверяем доступ
    if callback.from_user.id != d["owner_id"]:
        await callback.message.answer("❌ Нет доступа")
        return
    
    # Проверяем статус
    if d["status"] not in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        await callback.message.answer("❌ Нельзя изменить реквизиты для этой сделки")
        return
    
        from database import get_saved_payment_details
    saved_payment_details = await get_saved_payment_details(callback.from_user.id)
    
    buttons = []
    
    if saved_payment_details:
        buttons.append([InlineKeyboardButton(text="✏️ Ввести новые реквизиты", callback_data=f"sell:enter_new_payment:{deal_id}")])
        for detail in saved_payment_details:
            buttons.append([InlineKeyboardButton(text=f"📋 {detail['title']}", callback_data=f"sell:use_payment:{deal_id}:{detail['id']}")])
        else:
            buttons.append([InlineKeyboardButton(text="✏️ Ввести реквизиты", callback_data=f"sell:enter_new_payment:{deal_id}")])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:actions:{deal_id}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    current_payment_details = d.get("payment_details")
    
    if current_payment_details:
        text = (
            f"💳 <b>Изменение реквизитов для сделки #{deal_id}</b> 💳\n\n"
            f"📋 <b>Текущие реквизиты:</b>\n"
            f"<code>{current_payment_details}</code>\n\n"
            f"💡 <b>Выберите новые реквизиты:</b>"
        )
    else:
        text = (
            f"💳 <b>Указание реквизитов для сделки #{deal_id}</b> 💳\n\n"
            f"💡 <b>Выберите реквизиты для получения оплаты:</b>"
        )
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("deal:view_payment:"))
async def deal_view_payment(callback: CallbackQuery):
    """Просмотр текущих реквизитов"""
    await callback.answer()  # Сразу убираем индикатор загрузки
    
    try:
        deal_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError, AttributeError):
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("❌ Сделка не найдена")
        return
    
    # Проверяем доступ
    if callback.from_user.id != d["owner_id"]:
        await callback.message.answer("❌ Нет доступа")
        return
    
    payment_details = d.get("payment_details")
    
    if not payment_details:
        await callback.message.answer("❌ Реквизиты не указаны")
        return
    
    text = (
        f"💳 <b>Реквизиты для сделки #{deal_id}</b> 💳\n\n"
        f"📋 <b>Текущие реквизиты:</b>\n"
        f"<code>{payment_details}</code>\n\n"
        f"💡 <b>Чтобы изменить реквизиты, вернитесь в меню действий.</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"deal:actions:payment:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"deal:actions:{deal_id}")]
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("deal:info:"))
async def deal_info(callback: CallbackQuery):
    """Информация о сделке"""
    await callback.answer()  # Сразу убираем индикатор загрузки
    
    try:
        deal_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError, AttributeError):
        await callback.message.answer("❌ Неверный ID сделки")
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.message.answer("❌ Сделка не найдена")
        return
    
    # Проверяем доступ
    if callback.from_user.id not in [d["owner_id"], d.get("partner_id")]:
        await callback.message.answer("❌ Нет доступа")
        return
    
    role = get_role_name(callback.from_user.id, d)
    status_emoji = get_status_emoji(d["status"])
    status_name = get_status_name(d["status"])
    
    lines = [
        f"ℹ️ <b>Информация о сделке #{deal_id}</b> ℹ️",
        "",
        f"👤 <b>Ваша роль:</b> {role}",
        f"📊 <b>Статус:</b> {status_emoji} {status_name}",
        f"📋 <b>Тип:</b> {'Автогарант' if (d.get('side') or '').upper()=='GARANT' else ('Покупка' if d['side']=='BUY' else 'Продажа')}",
        "",
    ]
    
    # Информация о сумме
    if d.get("buy_amount"):
        lines.append(f"💰 <b>Сумма сделки:</b> {d['buy_amount']} BTC")
    else:
        lines.append(f"💰 <b>Лимиты:</b> {d['min_btc']} — {d['max_btc']} BTC")
    
    lines.append(f"📅 <b>Создана:</b> {format_datetime(d['created_at'])}")
    
    if d.get("matched_at"):
        lines.append(f"🤝 <b>Найден контрагент:</b> {format_datetime(d['matched_at'])}")
    
    if d.get("payment_marked_at"):
        lines.append(f"💳 <b>Оплата отмечена:</b> {format_datetime(d['payment_marked_at'])}")
    
    if d.get("seller_confirmed_at"):
        lines.append(f"✅ <b>Подтверждено продавцом:</b> {format_datetime(d['seller_confirmed_at'])}")
    
    if d.get("completed_at"):
        lines.append(f"🎉 <b>Завершена:</b> {format_datetime(d['completed_at'])}")
    
    lines.append("")
    
    # Информация о реквизитах (только для продавца)
    if d.get("payment_details") and role == "Продавец":
        lines.append(f"💳 <b>Ваши реквизиты:</b>\n<code>{d['payment_details']}</code>")
        lines.append("")
    
    # Кошелек покупателя убран - не нужен продавцу
    
    # Эскроу адрес
    if d.get("escrow_address") and role == "Продавец":
        lines.append(f"🔗 <b>Эскроу адрес:</b>\n<code>{d['escrow_address']}</code>")
        lines.append("")
    
    # Кнопка назад
    is_owner = callback.from_user.id == d["owner_id"]
    if is_owner and d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED]:
        back_to = f"deal:actions:{deal_id}"
    else:
        back_to = f"mydeal:view:{deal_id}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_to)]
    ])
    
    text = "\n".join(lines)
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


