"""
Обработчики админ-панели
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import (
    get_deal, update_deal_status, send_notification, send_system_message,
    is_admin, inc_success, update_user_stats_on_deal_complete,
    update_daily_stats, get_daily_stats, get_period_stats,
    get_problematic_deals, get_disputed_deals, get_active_deposits, DealStatus, add_deal_message,
    get_deal_messages, get_chat_participants, can_access_chat, get_user,
    calculate_seller_amounts, calculate_buyer_amounts, calculate_cancellation_refund,
    update_user_stats_on_cancel, get_all_users, get_all_users_count, get_user_statistics
)
from keyboards import admin_main_kb, rate_partner_kb
from utils import format_admin_stats, format_chat_message, get_status_name
from config import ADMIN_IDS
import logging

logger = logging.getLogger(__name__)

router = Router()


# ==================== FSM СОСТОЯНИЯ ДЛЯ АДМИНОВ ====================

class AdminChatStates(StatesGroup):
    WAIT_ADMIN_MESSAGE = State()


# ==================== ФИЛЬТР АДМИНОВ ====================

async def admin_filter(message_or_callback):
    """Проверка что пользователь - админ"""
    user_id = message_or_callback.from_user.id
    if user_id not in ADMIN_IDS:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer("❌ Доступно только администраторам")
        else:
            await message_or_callback.answer("❌ Доступно только администраторам", show_alert=True)
        return False
    return True


# ==================== АДМИН КОМАНДЫ ====================

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Команда /admin - админ панель"""
    if not await admin_filter(message):
        return
    
    await message.answer(
        "🔐 <b>Админ-панель</b>",
        reply_markup=admin_main_kb(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery):
    """Статистика"""
    if not await admin_filter(callback):
        return
    
    # Статистика за неделю
    stats_7d = await get_period_stats(7)
    stats_30d = await get_period_stats(30)
    
    text = (
        "📊 <b>Статистика платформы</b>\n\n"
        "📅 <b>За последние 7 дней:</b>\n"
        f"{format_admin_stats(stats_7d)}\n\n"
        "📅 <b>За последние 30 дней:</b>\n"
        f"{format_admin_stats(stats_30d)}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:stats")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")]
    ])
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:disputes")
async def admin_disputes(callback: CallbackQuery):
    """Список споров"""
    if not await admin_filter(callback):
        return
    
    disputes = await get_disputed_deals()
    
    if not disputes:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:disputes")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")]
        ])
        await callback.message.edit_text(
            "✅ <b>Активных споров нет</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    # Формируем список споров с кнопками
    rows = []
    for dispute in disputes:
        deal_id = dispute['id']
        owner_id = dispute.get('owner_id', 'N/A')
        partner_id = dispute.get('partner_id', 'N/A')
        created_at = dispute.get('created_at', 'N/A')
        
        # Форматируем дату
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(created_at)
            date_str = dt.strftime("%d.%m.%Y %H:%M")
        except:
            date_str = created_at
        
        label = f"🔴 Спор #{deal_id} | {date_str}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:dispute:view:{deal_id}")])
    
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:disputes")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    
    await callback.message.edit_text(
        f"⚠️ <b>Активные споры ({len(disputes)}):</b>\n\n"
        f"💡 <b>Выберите спор для просмотра и принятия решения</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:dispute:view:"))
async def admin_dispute_view(callback: CallbackQuery):
    """Просмотр деталей спора"""
    if not await admin_filter(callback):
        return
    
    try:
        deal_id = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    if d.get("status") != DealStatus.DISPUTED:
        await callback.answer("Это не спор", show_alert=True)
        return
    
    # Получаем информацию об участниках
    owner = await get_user(d["owner_id"])
    partner = await get_user(d.get("partner_id")) if d.get("partner_id") else None
    
    owner_name = owner.get("display_name") if owner else f"ID {d['owner_id']}"
    partner_name = partner.get("display_name") if partner else (f"ID {d['partner_id']}" if d.get("partner_id") else "Нет")
    
    # Форматируем даты
    from utils import format_datetime
    created_at = format_datetime(d.get("created_at", ""))
    matched_at = format_datetime(d.get("matched_at", "")) if d.get("matched_at") else "Не указано"
    
    # Рассчитываем суммы
    from decimal import Decimal
    from utils import get_btc_rub_rate, btc_to_rub
    max_btc = Decimal(d.get("buy_amount") or d["max_btc"])
    btc_rub_rate = await get_btc_rub_rate()
    
    seller_amounts = calculate_seller_amounts(max_btc)
    buyer_amounts = calculate_buyer_amounts(max_btc)
    
    max_btc_rub = btc_to_rub(max_btc, btc_rub_rate)
    max_btc_rub_formatted = f"{max_btc_rub:,.0f}".replace(",", " ")
    
    text = (
        f"🔴 <b>Спор #{deal_id}</b>\n\n"
        f"📅 <b>Создана:</b> {created_at}\n"
        f"🤝 <b>Начата:</b> {matched_at}\n\n"
        f"👤 <b>Владелец (Продавец):</b> {owner_name} (ID: {d['owner_id']})\n"
        f"👤 <b>Партнёр (Покупатель):</b> {partner_name} (ID: {d.get('partner_id', 'N/A')})\n\n"
        f"💰 <b>Сумма сделки:</b> {max_btc:.8f} BTC (~{max_btc_rub_formatted} руб)\n"
        f"💸 <b>Продавец получит:</b> {seller_amounts['net_to_seller']:.8f} BTC (полная сумма, которую платит покупатель)\n"
        f"💵 <b>Покупатель заплатит:</b> {buyer_amounts['buyer_payment']:.8f} BTC\n"
        f"💵 <b>Покупатель получит:</b> {buyer_amounts['buyer_receive']:.8f} BTC (100% от суммы покупки)\n"
        f"📉 <b>Комиссия платформы:</b> {buyer_amounts['buyer_commission']:.8f} BTC (0% - временно)\n\n"
    )
    
    # Добавляем информацию о реквизитах и кошельке
    if d.get("payment_details"):
        text += f"💳 <b>Реквизиты продавца:</b>\n<code>{d['payment_details']}</code>\n\n"
    
    # Получаем кошелек покупателя (сначала из сделки, потом из профиля)
    from database import get_buyer_wallet
    buyer_wallet = await get_buyer_wallet(d)
    if buyer_wallet:
        text += f"💼 <b>Кошелёк покупателя:</b>\n<code>{buyer_wallet}</code>\n\n"
    else:
        text += f"⚠️ <b>Кошелёк покупателя:</b> не указан\n\n"
    
    # Кнопки действий
    buttons = [
        [InlineKeyboardButton(text="✅ Релиз BTC покупателю", callback_data=f"admin:dispute:release:{deal_id}")],
        [InlineKeyboardButton(text="↩️ Возврат депозита продавцу", callback_data=f"admin:dispute:refund:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"admin:dispute:cancel:{deal_id}")],
        [InlineKeyboardButton(text="💬 Просмотр чата", callback_data=f"admin:dispute:chat:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Назад к спорам", callback_data="admin:disputes")]
    ]
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    
    await callback.answer()


@router.callback_query(F.data.startswith("admin:dispute:release:"))
async def admin_dispute_release(callback: CallbackQuery):
    """Админ решает спор: релиз BTC покупателю"""
    if not await admin_filter(callback):
        return
    
    try:
        deal_id = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d or d.get("status") != DealStatus.DISPUTED:
        await callback.answer("Спор не найден", show_alert=True)
        return
    
    # Обновляем статус на RELEASED
    await update_deal_status(deal_id, DealStatus.RELEASED)
    
    # Обновляем статистику
    await inc_success(d["owner_id"])
    if d.get("partner_id"):
        await inc_success(d["partner_id"])
    await update_user_stats_on_deal_complete(deal_id)
    await update_daily_stats()
    
    # Определяем покупателя и продавца
    buyer_id = d["partner_id"] if d["side"] == "SELL" else d["owner_id"]
    seller_id = d["owner_id"] if d["side"] == "SELL" else d["partner_id"]
    
    # Получаем кошелек покупателя (сначала из сделки, потом из профиля)
    from database import get_buyer_wallet
    buyer_wallet = await get_buyer_wallet(d)
    
    # Если кошелек не найден, пытаемся получить его из профиля и обновить сделку
    if not buyer_wallet:
        buyer_user = await get_user(buyer_id)
        if buyer_user and buyer_user.get("btc_wallet"):
            buyer_wallet = buyer_user["btc_wallet"]
            from database import set_deal_buyer_wallet
            await set_deal_buyer_wallet(deal_id, buyer_wallet)
            # Перезагружаем сделку
            d = await get_deal(deal_id)
    
    buyer_wallet_display = buyer_wallet if buyer_wallet else "не указан"
    
    # Уведомления участникам
    buyer_notification = (
        "✅ <b>Спор решён в вашу пользу!</b>\n\n"
        f"Сделка #{deal_id}\n"
        f"Администратор принял решение: BTC будут переведены вам.\n"
        f"Кошелёк: <code>{buyer_wallet_display}</code>"
    )
    await send_notification(
        callback.message.bot,
        buyer_id,
        buyer_notification,
        reply_markup=rate_partner_kb(deal_id, seller_id) if seller_id else None
    )
    
    if seller_id:
        seller_notification = (
            "⚠️ <b>Спор решён</b>\n\n"
            f"Сделка #{deal_id}\n"
            "Администратор принял решение в пользу покупателя.\n"
            "BTC будут переведены покупателю."
        )
        await send_notification(callback.message.bot, seller_id, seller_notification)
    
    # Системное сообщение в чат
    await send_system_message(deal_id, "👨‍⚖️ Администратор принял решение: BTC будут переведены покупателю.")
    
    buyer_wallet_info = f"\nКошелёк: <code>{buyer_wallet_display}</code>" if buyer_wallet else "\n⚠️ Кошелёк покупателя не указан"
    
    await callback.message.edit_text(
        f"✅ <b>Решение принято</b>\n\n"
        f"Сделка #{deal_id}\n"
        f"BTC будут переведены покупателю (ID: {buyer_id}){buyer_wallet_info}",
        parse_mode="HTML"
    )
    await callback.answer("Решение принято")


@router.callback_query(F.data.startswith("admin:dispute:refund:"))
async def admin_dispute_refund(callback: CallbackQuery):
    """Админ решает спор: возврат депозита продавцу"""
    if not await admin_filter(callback):
        return
    
    try:
        deal_id = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d or d.get("status") != DealStatus.DISPUTED:
        await callback.answer("Спор не найден", show_alert=True)
        return
    
    # Обновляем статус на CANCELLED
    await update_deal_status(deal_id, DealStatus.CANCELLED)
    
    # Обновляем статистику
    await update_user_stats_on_cancel(d["owner_id"])
    await update_daily_stats()
    
    # Определяем покупателя и продавца
    buyer_id = d["partner_id"] if d["side"] == "SELL" else d["owner_id"]
    seller_id = d["owner_id"] if d["side"] == "SELL" else d["partner_id"]
    
    # Уведомления участникам
    seller_notification = (
        "✅ <b>Спор решён в вашу пользу!</b>\n\n"
        f"Сделка #{deal_id}\n"
        "Администратор принял решение: депозит будет возвращён вам."
    )
    await send_notification(callback.message.bot, seller_id, seller_notification)
    
    if buyer_id:
        buyer_notification = (
            "⚠️ <b>Спор решён</b>\n\n"
            f"Сделка #{deal_id}\n"
            "Администратор принял решение в пользу продавца.\n"
            "Депозит будет возвращён продавцу."
        )
        await send_notification(callback.message.bot, buyer_id, buyer_notification)
    
    # Системное сообщение в чат
    await send_system_message(deal_id, "👨‍⚖️ Администратор принял решение: депозит будет возвращён продавцу.")
    
    await callback.message.edit_text(
        f"✅ <b>Решение принято</b>\n\n"
        f"Сделка #{deal_id}\n"
        f"Депозит будет возвращён продавцу (ID: {seller_id})",
        parse_mode="HTML"
    )
    await callback.answer("Решение принято")


@router.callback_query(F.data.startswith("admin:dispute:cancel:"))
async def admin_dispute_cancel(callback: CallbackQuery):
    """Админ решает спор: отмена сделки"""
    if not await admin_filter(callback):
        return
    
    try:
        deal_id = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d or d.get("status") != DealStatus.DISPUTED:
        await callback.answer("Спор не найден", show_alert=True)
        return
    
    # Обновляем статус на CANCELLED
    await update_deal_status(deal_id, DealStatus.CANCELLED)
    
    # Обновляем статистику
    await update_user_stats_on_cancel(d["owner_id"])
    if d.get("partner_id"):
        await update_user_stats_on_cancel(d["partner_id"])
    await update_daily_stats()
    
    # Определяем участников
    buyer_id = d["partner_id"] if d["side"] == "SELL" else d["owner_id"]
    seller_id = d["owner_id"] if d["side"] == "SELL" else d["partner_id"]
    
    # Уведомления участникам
    notification = (
        "❌ <b>Спор решён: сделка отменена</b>\n\n"
        f"Сделка #{deal_id}\n"
        "Администратор принял решение об отмене сделки."
    )
    
    await send_notification(callback.message.bot, seller_id, notification)
    if buyer_id:
        await send_notification(callback.message.bot, buyer_id, notification)
    
    # Системное сообщение в чат
    await send_system_message(deal_id, "👨‍⚖️ Администратор принял решение: сделка отменена.")
    
    await callback.message.edit_text(
        f"✅ <b>Решение принято</b>\n\n"
        f"Сделка #{deal_id} отменена",
        parse_mode="HTML"
    )
    await callback.answer("Решение принято")


@router.callback_query(F.data.startswith("admin:dispute:chat:"))
async def admin_dispute_chat(callback: CallbackQuery, state: FSMContext):
    """Просмотр чата спора"""
    if not await admin_filter(callback):
        return
    
    try:
        deal_id = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    # Используем существующую команду /chat
    from aiogram.types import Message
    # Создаем фейковое сообщение для использования существующего обработчика
    # Но проще просто перенаправить на команду /chat
    await callback.answer("💡 Используйте команду /chat_" + str(deal_id), show_alert=True)
    
    # Или сразу открываем чат
    await state.set_state(AdminChatStates.WAIT_ADMIN_MESSAGE)
    await state.update_data(deal_id=deal_id)
    
    messages = await get_deal_messages(deal_id, limit=50)
    
    if not messages:
        text = f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📭 <b>Чат пуст.</b>"
    else:
        lines = [f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📋 <b>Последние сообщения:</b>\n"]
        for msg in messages[-20:]:
            lines.append(format_chat_message(msg))
        text = "\n".join(lines)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:dispute:chat:{deal_id}")],
        [InlineKeyboardButton(text="◀️ К спору", callback_data=f"admin:dispute:view:{deal_id}")]
    ])
    
    try:
        await callback.message.edit_text(
            f"{text}\n\n"
            "✨ <b>Вы подключены к чату как администратор</b>\n"
            "💬 <b>Просто отправьте текст сообщения, и оно будет добавлено в чат</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            f"{text}\n\n"
            "✨ <b>Вы подключены к чату как администратор</b>\n"
            "💬 <b>Просто отправьте текст сообщения, и оно будет добавлено в чат</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    
    await callback.answer()


@router.callback_query(F.data == "admin:problems")
async def admin_problems(callback: CallbackQuery):
    """Проблемные сделки"""
    if not await admin_filter(callback):
        return
    
    problems = await get_problematic_deals()
    
    if not problems:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")]
        ])
        await callback.message.edit_text(
            "✅ Проблемных сделок нет",
            reply_markup=kb
        )
        await callback.answer()
        return
    
    lines = ["⚠️ <b>Проблемные сделки:</b>\n"]
    
    for issue in problems:
        issue_type = "🔴 СПОР" if issue["issue_type"] == "DISPUTED" else "⏰ ДОЛГАЯ"
        lines.append(
            f"\n{issue_type} Сделка #{issue['id']}\n"
            f"Статус: {issue['status']}\n"
            f"Владелец: {issue['owner_id']}\n"
            f"Партнёр: {issue.get('partner_id') or 'нет'}"
        )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:problems")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")]
    ])
    
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin:back")
async def admin_back(callback: CallbackQuery):
    """Назад в админ меню"""
    if not await admin_filter(callback):
        return
    
    await callback.message.edit_text(
        "🔐 <b>Админ-панель</b>",
        reply_markup=admin_main_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:manual_withdraw_done:"))
async def admin_manual_withdraw_done(callback: CallbackQuery):
    """Обработчик кнопки "Выполнен ручной вывод" """
    if not await admin_filter(callback):
        return
    
    try:
        deal_id = int(callback.data.split(":")[-1])
        deal = await get_deal(deal_id)
        
        if not deal:
            await callback.answer("❌ Сделка не найдена", show_alert=True)
            return
        
        # Завершаем сделку по успешному сценарию
        from database import complete_deal_after_withdrawal_confirmation
        from config import BOT_TOKEN
        from utils import create_bot
        
        bot = create_bot(BOT_TOKEN)
        try:
            success = await complete_deal_after_withdrawal_confirmation(deal_id, bot=bot)
            
            if success:
                # Обновляем сообщение, убирая кнопки и добавляя отметку о выполнении
                text = callback.message.text
                text += f"\n\n✅ <b>Ручной вывод выполнен администратором</b>\n"
                text += f"👤 Администратор: @{callback.from_user.username or callback.from_user.first_name}\n"
                text += f"✅ <b>Сделка успешно завершена!</b>"
                
                await callback.message.edit_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=None
                )
                await callback.answer("✅ Сделка успешно завершена!")
            else:
                await callback.answer("⚠️ Не удалось завершить сделку. Проверьте логи.", show_alert=True)
        finally:
            await bot.session.close()
        
    except Exception as e:
        logger.error(f"Ошибка при обработке admin:manual_withdraw_done: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("admin:deal_info:"))
async def admin_deal_info(callback: CallbackQuery):
    """Обработчик кнопки "Детали сделки" """
    if not await admin_filter(callback):
        return
    
    try:
        deal_id = int(callback.data.split(":")[-1])
        deal = await get_deal(deal_id)
        
        if not deal:
            await callback.answer("❌ Сделка не найдена", show_alert=True)
            return
        
        from utils import format_datetime
        status_name = await get_status_name(deal['status'])
        
        # Получаем информацию о пользователях
        # Определяем продавца и покупателя на основе side
        owner_id = deal.get('owner_id', 0)
        partner_id = deal.get('partner_id', 0)
        side = deal.get('side', 'SELL')
        
        if side == 'SELL':
            seller_id = owner_id
            buyer_id = partner_id
        else:
            seller_id = partner_id
            buyer_id = owner_id
        
        seller = await get_user(seller_id) if seller_id else None
        buyer = await get_user(buyer_id) if buyer_id else None
        
        seller_info = f"@{seller.get('username', 'N/A')}" if seller else "N/A"
        buyer_info = f"@{buyer.get('username', 'N/A')}" if buyer else "N/A"
        
        created_at = deal.get('created_at')
        if created_at:
            try:
                created_at_str = format_datetime(created_at)
            except:
                created_at_str = str(created_at)
        else:
            created_at_str = "N/A"
        
        text = (
            f"📋 <b>Детали сделки #{deal_id}</b>\n\n"
            f"👤 <b>Продавец:</b> {seller_info} (ID: {seller_id or 'N/A'})\n"
            f"👤 <b>Покупатель:</b> {buyer_info} (ID: {buyer_id or 'N/A'})\n"
            f"💰 <b>Сумма:</b> {deal.get('amount', deal.get('max_btc', 'N/A'))} BTC\n"
            f"📊 <b>Статус:</b> {status_name}\n"
            f"📅 <b>Создана:</b> {created_at_str}\n"
        )
        
        if deal.get('escrow_address'):
            text += f"💳 <b>Кошелек эскроу:</b> <code>{deal['escrow_address']}</code>\n"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")]
        ])
        
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка при обработке admin:deal_info: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("admin:user_info:"))
async def admin_user_info(callback: CallbackQuery):
    """Обработчик кнопки "Профиль пользователя" """
    if not await admin_filter(callback):
        return
    
    try:
        user_id = int(callback.data.split(":")[-1])
        user = await get_user(user_id)
        
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        stats = await get_user_statistics(user_id)
        
        text = (
            f"👤 <b>Профиль пользователя</b>\n\n"
            f"🆔 <b>ID:</b> {user_id}\n"
            f"📛 <b>Имя:</b> {user.get('name', 'N/A')}\n"
            f"📞 <b>Username:</b> @{user.get('username', 'N/A')}\n"
            f"💳 <b>BTC кошелек:</b> <code>{user.get('btc_wallet', 'не указан')}</code>\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"✅ Успешных сделок: {stats.get('successful_deals', 0)}\n"
            f"❌ Отменено: {stats.get('cancelled_deals', 0)}\n"
            f"⚖️ Споров: {stats.get('disputed_deals', 0)}\n"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")]
        ])
        
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка при обработке admin:user_info: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# ==================== ВСЕ ПОЛЬЗОВАТЕЛИ ====================

@router.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery):
    """Список всех пользователей"""
    if not await admin_filter(callback):
        return
    
    await _show_users_page(callback, 1)


@router.callback_query(F.data.startswith("admin:users:page:"))
async def admin_users_page(callback: CallbackQuery):
    """Список пользователей (конкретная страница)"""
    if not await admin_filter(callback):
        return
    
    try:
        page = int(callback.data.split(":")[-1])
        if page < 1:
            page = 1
        if page > 1000:
            page = 1000
    except (ValueError, IndexError):
        page = 1
    
    await callback.answer()
    await _show_users_page(callback, page)


async def _show_users_page(callback: CallbackQuery, page: int):
    """Показать страницу списка пользователей"""
    USERS_PER_PAGE = 10
    MAX_PAGES = 1000
    
    # Получаем общее количество пользователей
    total_users = await get_all_users_count()
    
    if total_users == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:users")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")]
        ])
        await callback.message.edit_text(
            "👥 <b>Все пользователи</b>\n\n"
            "Нет зарегистрированных пользователей",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return
    
    # Рассчитываем количество страниц
    total_pages = min((total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE, MAX_PAGES)
    
    # Ограничиваем номер страницы
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    
    # Получаем пользователей для текущей страницы
    offset = (page - 1) * USERS_PER_PAGE
    users = await get_all_users(limit=USERS_PER_PAGE, offset=offset)
    
    lines = [f"👥 <b>Все пользователи</b>\n\n📊 Всего: {total_users}\n"]
    
    buttons = []
    for user in users:
        user_id = user['tg_id']
        username = user.get('username') or 'Нет username'
        display_name = user.get('display_name') or f"ID {user_id}"
        
        # Получаем статистику пользователя
        stats = await get_user_statistics(user_id)
        if stats:
            total_deals = stats.get('total_deals', 0)
            success_count = stats.get('success_count', 0)
            rating = stats.get('average_rating', 0.0)
        else:
            total_deals = 0
            success_count = 0
            rating = 0.0
        
        lines.append(
            f"👤 <b>{display_name}</b>\n"
            f"   @{username}\n"
            f"   ID: {user_id}\n"
            f"   Сделок: {total_deals} (успешных: {success_count})\n"
            f"   Рейтинг: {rating:.1f}/5.0\n"
        )
        
        buttons.append([InlineKeyboardButton(
            text=f"👤 {display_name} (ID: {user_id})",
            callback_data=f"admin:user:view:{user_id}"
        )])
    
    # Кнопки навигации
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin:users:page:{page - 1}"))
    
    nav_buttons.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="admin:users:info"))
    
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"admin:users:page:{page + 1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:users")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4000] + "\n\n... (показаны первые 10 пользователей)"
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "admin:users:info")
async def admin_users_info(callback: CallbackQuery):
    """Информация о странице пользователей"""
    await callback.answer("Текущая страница", show_alert=False)


# ==================== АКТИВНЫЕ ДЕПОЗИТЫ ====================

@router.callback_query(F.data == "admin:active_deposits")
async def admin_active_deposits(callback: CallbackQuery):
    """Список активных депозитов"""
    if not await admin_filter(callback):
        return
    
    deposits = await get_active_deposits()
    
    if not deposits:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:active_deposits")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")]
        ])
        await callback.message.edit_text(
            "💰 <b>Активные депозиты</b>\n\n"
            "Нет активных депозитов",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    from decimal import Decimal
    from utils import get_btc_rub_rate, btc_to_rub
    from datetime import datetime
    
    btc_rub_rate = await get_btc_rub_rate()
    lines = ["💰 <b>Активные депозиты</b>\n"]
    
    buttons = []
    for d in deposits[:20]:  # Показываем максимум 20 депозитов
        deal_id = d['id']
        status = d.get('status', '')
        status_name = get_status_name(status)
        
        # Определяем сумму депозита
        if d.get("buy_amount"):
            deposit_btc = Decimal(d['buy_amount'])
        else:
            deposit_btc = Decimal(d['max_btc'])
        
        deposit_rub = btc_to_rub(deposit_btc, btc_rub_rate)
        deposit_rub_formatted = f"{deposit_rub:,.0f}".replace(",", " ")
        deposit_btc_formatted = f"{deposit_btc:.8f}".rstrip('0').rstrip('.')
        
        # Получаем информацию о пользователе
        owner = await get_user(d['owner_id'])
        owner_name = owner.get('display_name') if owner else f"ID {d['owner_id']}"
        
        # Форматируем дату
        created = datetime.fromisoformat(d['created_at']).strftime("%d.%m.%Y %H:%M")
        
        lines.append(
            f"\n📋 <b>Сделка #{deal_id}</b>\n"
            f"👤 <b>Пользователь:</b> {owner_name}\n"
            f"💰 <b>Депозит:</b> {deposit_btc_formatted} BTC ({deposit_rub_formatted} руб)\n"
            f"📊 <b>Статус:</b> {status_name}\n"
            f"📅 <b>Создана:</b> {created}"
        )
        
        buttons.append([InlineKeyboardButton(
            text=f"💰 Вернуть депозит #{deal_id}",
            callback_data=f"admin:refund_deposit:{deal_id}"
        )])
    
    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:active_deposits")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin:back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4000] + "\n\n... (показаны первые 20 депозитов)"
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:refund_deposit:"))
async def admin_refund_deposit(callback: CallbackQuery):
    """Админ возвращает депозит пользователю"""
    if not await admin_filter(callback):
        return
    
    try:
        deal_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return
    
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return
    
    if d.get("status") not in [DealStatus.AWAITING_DEPOSIT, DealStatus.ESCROWED, DealStatus.CONFIRMING, 
                                DealStatus.ACTIVE, DealStatus.MATCHED, DealStatus.PAYMENT_PENDING]:
        await callback.answer("❌ Это не активный депозит", show_alert=True)
        return
    
    # Определяем сумму депозита (полный возврат без комиссии)
    from decimal import Decimal
    if d.get("buy_amount"):
        deposit_amount = Decimal(d['buy_amount'])
    else:
        deposit_amount = Decimal(d['max_btc'])
    
    # Получаем кошелек пользователя
    user = await get_user(d['owner_id'])
    user_wallet = user.get("btc_wallet") if user else None
    
    if not user_wallet:
        await callback.answer("❌ У пользователя не указан BTC кошелек", show_alert=True)
        return
    
    # Выполняем возврат депозита через очередь (как при обычной отмене)
    import logging
    import json
    import asyncio
    from pathlib import Path
    logger = logging.getLogger(__name__)
    logger.info(f"💰 [АДМИН ВОЗВРАТ] Начало возврата депозита для сделки {deal_id}, кошелек: {user_wallet}, сумма: {deposit_amount} BTC")
    
    try:
        amount_str = f"{deposit_amount:.8f}".rstrip('0').rstrip('.')
        from withdraw_queue import enqueue_payout
        queued = await enqueue_payout(
            deal_id=deal_id,
            user_id=d['owner_id'],
            wallet=user_wallet,
            amount=amount_str,
            payout_type="refund",
            admin_refund=True,
        )
        if queued.get("created"):
            logger.info(f"✅ [АДМИН ВОЗВРАТ] Задача возврата депозита сохранена для сделки #{deal_id}")
        else:
            logger.warning(f"⚠️ [АДМИН ВОЗВРАТ] Задача для сделки {deal_id} уже существует, дубликат пропущен")
        
        # Обновляем статус сделки на отмененную
        await update_deal_status(deal_id, DealStatus.CANCELLED)
        
        # Обновляем статистику
        await update_user_stats_on_cancel(d['owner_id'])
        await update_daily_stats()
        
        # Уведомляем пользователя
        user_notification = (
            "✅ <b>Депозит возвращен</b>\n\n"
            f"Сделка #{deal_id}\n"
            f"Сумма: {amount_str} BTC\n"
            "Администратор вернул ваш депозит.\n\n"
            "⏳ Депозит будет возвращен автоматически."
        )
        
        await send_notification(callback.message.bot, d['owner_id'], user_notification)
        
        await callback.answer("✅ Задача возврата депозита добавлена в очередь", show_alert=True)
        
        # Обновляем список активных депозитов
        await admin_active_deposits(callback)
        
    except Exception as e:
        logger.error(f"❌ [АДМИН ВОЗВРАТ] Исключение при возврате депозита: {e}", exc_info=True)
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


# ==================== ПОДТВЕРЖДЕНИЕ ДЕПОЗИТА ====================

@router.callback_query(F.data.startswith("admin:confirm:"))
async def admin_confirm_deposit(callback: CallbackQuery):
    """Админ подтверждает депозит"""
    if not await admin_filter(callback):
        return
    
    import logging
    logger = logging.getLogger(__name__)
    
    deal_id = int(callback.data.split(":")[2])
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    logger.info(f"🔍 Админ подтверждает депозит для сделки {deal_id}: текущий статус={d.get('status')}, payment_details={bool(d.get('payment_details'))}")
    
    # Используем функцию confirm_deposit, которая правильно проверит наличие реквизитов
    from database import confirm_deposit
    await confirm_deposit(deal_id)
    
    # Получаем обновленные данные сделки
    d = await get_deal(deal_id)
    if not d:
        await callback.answer("Ошибка при обновлении сделки", show_alert=True)
        return
    
    logger.info(f"📊 Статус сделки {deal_id} после подтверждения депозита: {d.get('status')}")
    
    # НЕ отправляем уведомление здесь - оно будет отправлено после нажатия кнопки "Депозит внесён" пользователем
    # Уведомление отправляется только в sell_deposit_confirmed после нажатия кнопки "Депозит внесён"
    
    if d.get("status") == DealStatus.ACTIVE:
        logger.info(f"✅ Сделка {deal_id} активирована администратором (депозит подтвержден, реквизиты указаны)")
    else:
        logger.info(f"⏳ Сделка {deal_id} переведена в статус ESCROWED (депозит подтвержден, реквизиты не указаны)")
    
    await callback.message.edit_text(
        f"✅ Депозит по сделке #{deal_id} подтверждён\n\n"
        f"📊 Статус сделки: {get_status_name(d.get('status'))}",
        parse_mode="HTML"
    )
    await callback.answer("Подтверждено")


@router.callback_query(F.data.startswith("admin:cancel:"))
async def admin_cancel_deposit(callback: CallbackQuery):
    """Админ отклоняет депозит"""
    if not await admin_filter(callback):
        return
    
    deal_id = int(callback.data.split(":")[2])
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    # Отменяем сделку
    await update_deal_status(deal_id, DealStatus.CANCELLED)
    
    # Уведомляем владельца
    notification = (
        "❌ <b>Депозит отклонён</b>\n\n"
        f"Сделка #{deal_id}\n"
        "Ваш депозит не был подтверждён администратором.\n"
        "Возможно, перевод не поступил или была ошибка.\n\n"
        "Свяжитесь с поддержкой для уточнения."
    )
    await send_notification(callback.message.bot, d["owner_id"], notification)
    
    await callback.message.edit_text(
        f"❌ Депозит по сделке #{deal_id} отклонён",
        parse_mode="HTML"
    )
    await callback.answer("Отклонено")


# ==================== РЕЛИЗ BTC ====================

@router.callback_query(F.data.startswith("admin:release:"))
async def admin_release(callback: CallbackQuery):
    """Админ делает релиз BTC"""
    if not await admin_filter(callback):
        return
    
    deal_id = int(callback.data.split(":")[2])
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    # Обновляем статус
    await update_deal_status(deal_id, DealStatus.RELEASED)
    
    # Обновляем статистику успешных сделок
    await inc_success(d["owner_id"])
    if d.get("partner_id"):
        await inc_success(d["partner_id"])
    
    # Обновляем общую статистику
    await update_user_stats_on_deal_complete(deal_id)
    await update_daily_stats()
    
    # Определяем покупателя
    buyer_id = d["partner_id"] if d["side"] == "SELL" else d["owner_id"]
    seller_id = d["owner_id"] if d["side"] == "SELL" else d["partner_id"]
    
    # Получаем кошелек покупателя (сначала из сделки, потом из профиля)
    from database import get_buyer_wallet
    buyer_wallet = await get_buyer_wallet(d)
    
    # Если кошелек не найден, пытаемся получить его из профиля и обновить сделку
    if not buyer_wallet:
        buyer_id_for_wallet = d["partner_id"] if d["side"] == "SELL" else d["owner_id"]
        buyer_user = await get_user(buyer_id_for_wallet)
        if buyer_user and buyer_user.get("btc_wallet"):
            buyer_wallet = buyer_user["btc_wallet"]
            from database import set_deal_buyer_wallet
            await set_deal_buyer_wallet(deal_id, buyer_wallet)
            # Перезагружаем сделку
            d = await get_deal(deal_id)
    
    buyer_wallet_display = buyer_wallet if buyer_wallet else "не указан"
    
    # 🎉 УВЕДОМЛЕНИЕ покупателю
    buyer_notification = (
        "🎉 <b>Сделка успешно завершена!</b>\n\n"
        f"Сделка #{deal_id}\n"
        f"BTC переведены на ваш кошелёк: `{buyer_wallet_display}`\n\n"
        "Спасибо за использование нашего сервиса!"
    )
    await send_notification(
        callback.message.bot,
        buyer_id,
        buyer_notification,
        reply_markup=rate_partner_kb(deal_id, seller_id) if seller_id else None
    )
    
    # 🎉 УВЕДОМЛЕНИЕ продавцу
    if seller_id:
        seller_notification = (
            "🎉 <b>Сделка успешно завершена!</b>\n\n"
            f"Сделка #{deal_id}\n"
            "Покупатель получил BTC.\n"
            "Вы получили оплату в фиате.\n\n"
            "Спасибо за использование нашего сервиса!"
        )
        await send_notification(
            callback.message.bot,
            seller_id,
            seller_notification,
            reply_markup=rate_partner_kb(deal_id, buyer_id)
        )
    
    await callback.message.edit_text(
        f"✅ Релиз по сделке #{deal_id} выполнен.\n"
        f"BTC отправлены на кошелёк: `{buyer_wallet_display}`",
        parse_mode="Markdown"
    )
    await callback.answer("Релиз выполнен")


@router.callback_query(F.data.startswith("admin:dispute:"))
async def admin_dispute(callback: CallbackQuery):
    """Админ переводит сделку в спор"""
    if not await admin_filter(callback):
        return
    
    deal_id = int(callback.data.split(":")[2])
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    # Переводим в спор
    await update_deal_status(deal_id, DealStatus.DISPUTED)
    
    # Уведомляем участников
    notification = (
        "⚠️ <b>Сделка переведена в спор</b>\n\n"
        f"Сделка #{deal_id}\n"
        "Администратор перевёл сделку в режим разбора.\n"
        "Ожидайте дальнейших инструкций."
    )
    
    await send_notification(callback.message.bot, d["owner_id"], notification)
    if d.get("partner_id"):
        await send_notification(callback.message.bot, d["partner_id"], notification)
    
    await callback.message.edit_text(
        f"⚠️ Сделка #{deal_id} переведена в спор",
        parse_mode="HTML"
    )
    await callback.answer("Переведено в спор")


# ==================== ПРОСМОТР ЧАТА СДЕЛКИ ====================

@router.message(Command("chat"))
async def cmd_chat_with_id(message: Message, state: FSMContext):
    """Команда /chat_123 - просмотр и подключение к чату сделки"""
    if not await admin_filter(message):
        return
    
    # Парсим ID сделки из команды
    parts = message.text.split("_")
    if len(parts) < 2:
        await message.answer(
            "❌ <b>Используйте:</b> <code>/chat_123</code>\n"
            "💡 <b>где 123 - ID сделки</b>\n\n"
            "📝 <b>После подключения просто отправьте текст сообщения в чат</b>",
            parse_mode="HTML"
        )
        return
    
    try:
        deal_id = int(parts[1])
    except ValueError:
        await message.answer("❌ <b>Неверный формат ID</b>\n\n💡 <b>Используйте:</b> <code>/chat_123</code>", parse_mode="HTML")
        return
    
    from database import get_deal_messages, can_access_chat, get_deal
    
    # Проверяем доступ
    if not await can_access_chat(message.from_user.id, deal_id):
        await message.answer("❌ <b>Нет доступа к чату этой сделки</b>", parse_mode="HTML")
        return
    
    # Получаем информацию о сделке
    d = await get_deal(deal_id)
    if not d:
        await message.answer("❌ <b>Сделка не найдена</b>", parse_mode="HTML")
        return
    
    # Устанавливаем состояние для отправки сообщений администратором
    await state.set_state(AdminChatStates.WAIT_ADMIN_MESSAGE)
    await state.update_data(deal_id=deal_id)
    
    # Получаем сообщения
    messages = await get_deal_messages(deal_id, limit=50)
    
    # Форматируем историю
    from utils import format_chat_message
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    if not messages:
        text = f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📭 <b>Чат пуст. Напишите первое сообщение!</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin:back")]
        ])
    else:
        lines = [f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📋 <b>Последние сообщения:</b>\n"]
        for msg in messages[-20:]:  # Последние 20 сообщений
            lines.append(format_chat_message(msg))
        text = "\n".join(lines)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:chat:refresh:{deal_id}")],
            [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin:back")]
        ])
    
    # Отправляем порциями если слишком длинно
    if len(text) > 4000:
        # Делим на части
        text_parts = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) > 4000:
                text_parts.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current:
            text_parts.append(current)
        
        for i, part in enumerate(text_parts):
            if i == len(text_parts) - 1:
                # Последняя часть с кнопками
                await message.answer(part, reply_markup=kb, parse_mode="HTML")
            else:
                await message.answer(part, parse_mode="HTML")
    else:
        await message.answer(
            f"{text}\n\n"
            "✨ <b>Вы подключены к чату как администратор</b>\n"
            "💬 <b>Просто отправьте текст сообщения, и оно будет добавлено в чат</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )


# ==================== ОТПРАВКА СООБЩЕНИЙ АДМИНИСТРАТОРОМ ====================

@router.message(AdminChatStates.WAIT_ADMIN_MESSAGE)
async def admin_chat_message(message: Message, state: FSMContext):
    """Обработка сообщений администратора в чате сделки"""
    if not await admin_filter(message):
        await state.clear()
        return
    
    data = await state.get_data()
    deal_id = data.get("deal_id")
    
    if not deal_id:
        await state.clear()
        await message.answer("❌ <b>Сессия истекла. Используйте команду /chat_n снова.</b>", parse_mode="HTML")
        return
    
    # Проверяем доступ
    if not await can_access_chat(message.from_user.id, deal_id):
        await state.clear()
        await message.answer("❌ <b>Нет доступа к чату этой сделки</b>", parse_mode="HTML")
        return
    
    if not message.text:
        await message.answer("❌ <b>Напишите сообщение текстом</b>", parse_mode="HTML")
        return

    text = message.text.strip()
    
    if not text:
        await message.answer("❌ <b>Сообщение не может быть пустым</b>", parse_mode="HTML")
        return
    
    # Пропускаем команды (они обрабатываются отдельными обработчиками)
    if text.startswith("/"):
        return
    
    # Добавляем сообщение в чат
    try:
        # Добавляем префикс для администратора (без HTML, так как будет отформатировано при отображении)
        admin_text = f"👨‍⚖️ Администратор: {text}"
        await add_deal_message(deal_id, message.from_user.id, admin_text)
        
        await message.answer(
            f"✅ <b>Сообщение отправлено в чат сделки #{deal_id}</b>\n\n"
            f"💬 <b>Текст:</b> {text}\n\n"
            f"💡 <b>Для выхода используйте:</b> <code>/exit</code>",
            parse_mode="HTML"
        )
        
        # Уведомляем участников сделки
        d = await get_deal(deal_id)
        if d:
            participants = []
            if d.get("owner_id"):
                participants.append(d["owner_id"])
            if d.get("partner_id") and d["partner_id"] not in participants:
                participants.append(d["partner_id"])
            
            notification = (
                f"👨‍⚖️ <b>Сообщение от администратора</b>\n\n"
                f"Сделка #{deal_id}\n\n"
                f"{text}"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")]
            ])
            
            for user_id in participants:
                if user_id != message.from_user.id:
                    try:
                        await send_notification(message.bot, user_id, notification, reply_markup=kb)
                    except Exception:
                        pass
        
    except Exception as e:
        await message.answer(f"❌ <b>Ошибка отправки сообщения:</b> {e}", parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:chat:refresh:"))
async def admin_chat_refresh(callback: CallbackQuery, state: FSMContext):
    """Обновление просмотра чата администратором"""
    if not await admin_filter(callback):
        return
    
    deal_id = int(callback.data.split(":")[3])
    
    # Проверяем доступ
    if not await can_access_chat(callback.from_user.id, deal_id):
        await callback.answer("❌ Нет доступа к чату этой сделки", show_alert=True)
        return
    
    # Получаем сообщения
    messages = await get_deal_messages(deal_id, limit=50)
    
    # Форматируем историю
    if not messages:
        text = f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📭 <b>Чат пуст.</b>"
    else:
        lines = [f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📋 <b>Последние сообщения:</b>\n"]
        for msg in messages[-20:]:
            lines.append(format_chat_message(msg))
        text = "\n".join(lines)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:chat:refresh:{deal_id}")],
        [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin:back")]
    ])
    
    try:
        await callback.message.edit_text(
            f"{text}\n\n"
            "✨ <b>Вы подключены к чату как администратор</b>\n"
            "💬 <b>Просто отправьте текст сообщения, и оно будет добавлено в чат</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            f"{text}\n\n"
            "✨ <b>Вы подключены к чату как администратор</b>\n"
            "💬 <b>Просто отправьте текст сообщения, и оно будет добавлено в чат</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    
    # Устанавливаем состояние
    await state.set_state(AdminChatStates.WAIT_ADMIN_MESSAGE)
    await state.update_data(deal_id=deal_id)
    
    await callback.answer("🔄 Обновлено")


@router.message(Command("exit"))
async def cmd_exit_admin_chat(message: Message, state: FSMContext):
    """Выход из режима отправки сообщений в чат"""
    if not await admin_filter(message):
        return
    
    data = await state.get_data()
    deal_id = data.get("deal_id")
    
    if deal_id:
        await state.clear()
        await message.answer(
            f"✅ <b>Вы вышли из режима отправки сообщений</b>\n\n"
            f"💡 <b>Используйте</b> <code>/chat_{deal_id}</code> <b>для повторного подключения</b>",
            parse_mode="HTML"
        )
    else:
        await state.clear()
        await message.answer("✅ <b>Режим отправки сообщений отключён</b>", parse_mode="HTML")


@router.message(Command("update_stats"))
async def cmd_update_stats(message: Message):
    """Команда /update_stats - принудительное обновление статистики"""
    if not await admin_filter(message):
        return
    
    await update_daily_stats()
    stats = await get_daily_stats()
    
    await message.answer(
        f"✅ Статистика обновлена\n\n"
        f"Дата: {stats['date']}\n"
        f"Всего сделок: {stats['total_deals']}\n"
        f"Успешных: {stats['successful_deals']}\n"
        f"Отменено: {stats['cancelled_deals']}\n"
        f"Споров: {stats['disputed_deals']}\n"
        f"Объём: {stats['total_volume_btc']} BTC\n"
        f"Комиссия: {stats['total_commission_btc']} BTC"
    )


# ==================== РЕЛИЗ BTC ====================

@router.callback_query(F.data.startswith("admin:release:"))
async def admin_release(callback: CallbackQuery):
    """Админ делает релиз BTC"""
    if not await admin_filter(callback):
        return
    
    deal_id = int(callback.data.split(":")[2])
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    # Обновляем статус
    await update_deal_status(deal_id, DealStatus.RELEASED)
    
    # Обновляем статистику успешных сделок
    await inc_success(d["owner_id"])
    if d.get("partner_id"):
        await inc_success(d["partner_id"])
    
    # Обновляем общую статистику
    await update_user_stats_on_deal_complete(deal_id)
    await update_daily_stats()
    
    # Определяем покупателя
    buyer_id = d["partner_id"] if d["side"] == "SELL" else d["owner_id"]
    seller_id = d["owner_id"] if d["side"] == "SELL" else d["partner_id"]
    
    # Получаем кошелек покупателя (сначала из сделки, потом из профиля)
    from database import get_buyer_wallet
    buyer_wallet = await get_buyer_wallet(d)
    
    # Если кошелек не найден, пытаемся получить его из профиля и обновить сделку
    if not buyer_wallet:
        buyer_id_for_wallet = d["partner_id"] if d["side"] == "SELL" else d["owner_id"]
        buyer_user = await get_user(buyer_id_for_wallet)
        if buyer_user and buyer_user.get("btc_wallet"):
            buyer_wallet = buyer_user["btc_wallet"]
            from database import set_deal_buyer_wallet
            await set_deal_buyer_wallet(deal_id, buyer_wallet)
            # Перезагружаем сделку
            d = await get_deal(deal_id)
    
    buyer_wallet_display = buyer_wallet if buyer_wallet else "не указан"
    
    # 🎉 УВЕДОМЛЕНИЕ покупателю
    buyer_notification = (
        "🎉 <b>Сделка успешно завершена!</b>\n\n"
        f"Сделка #{deal_id}\n"
        f"BTC переведены на ваш кошелёк: `{buyer_wallet_display}`\n\n"
        "Спасибо за использование нашего сервиса!"
    )
    await send_notification(
        callback.message.bot,
        buyer_id,
        buyer_notification,
        reply_markup=rate_partner_kb(deal_id, seller_id) if seller_id else None
    )
    
    # 🎉 УВЕДОМЛЕНИЕ продавцу
    if seller_id:
        seller_notification = (
            "🎉 <b>Сделка успешно завершена!</b>\n\n"
            f"Сделка #{deal_id}\n"
            "Покупатель получил BTC.\n"
            "Вы получили оплату в фиате.\n\n"
            "Спасибо за использование нашего сервиса!"
        )
        await send_notification(
            callback.message.bot,
            seller_id,
            seller_notification,
            reply_markup=rate_partner_kb(deal_id, buyer_id)
        )
    
    await callback.message.edit_text(
        f"✅ Релиз по сделке #{deal_id} выполнен.\n"
        f"BTC отправлены на кошелёк: `{buyer_wallet_display}`",
        parse_mode="Markdown"
    )
    await callback.answer("Релиз выполнен")


@router.callback_query(F.data.startswith("admin:dispute:"))
async def admin_dispute(callback: CallbackQuery):
    """Админ переводит сделку в спор"""
    if not await admin_filter(callback):
        return
    
    deal_id = int(callback.data.split(":")[2])
    d = await get_deal(deal_id)
    
    if not d:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    
    # Переводим в спор
    await update_deal_status(deal_id, DealStatus.DISPUTED)
    
    # Уведомляем участников
    notification = (
        "⚠️ <b>Сделка переведена в спор</b>\n\n"
        f"Сделка #{deal_id}\n"
        "Администратор перевёл сделку в режим разбора.\n"
        "Ожидайте дальнейших инструкций."
    )
    
    await send_notification(callback.message.bot, d["owner_id"], notification)
    if d.get("partner_id"):
        await send_notification(callback.message.bot, d["partner_id"], notification)
    
    await callback.message.edit_text(
        f"⚠️ Сделка #{deal_id} переведена в спор",
        parse_mode="HTML"
    )
    await callback.answer("Переведено в спор")


# ==================== ПРОСМОТР ЧАТА СДЕЛКИ ====================

@router.message(Command("chat"))
async def cmd_chat_with_id(message: Message, state: FSMContext):
    """Команда /chat_123 - просмотр и подключение к чату сделки"""
    if not await admin_filter(message):
        return
    
    # Парсим ID сделки из команды
    parts = message.text.split("_")
    if len(parts) < 2:
        await message.answer(
            "❌ <b>Используйте:</b> <code>/chat_123</code>\n"
            "💡 <b>где 123 - ID сделки</b>\n\n"
            "📝 <b>После подключения просто отправьте текст сообщения в чат</b>",
            parse_mode="HTML"
        )
        return
    
    try:
        deal_id = int(parts[1])
    except ValueError:
        await message.answer("❌ <b>Неверный формат ID</b>\n\n💡 <b>Используйте:</b> <code>/chat_123</code>", parse_mode="HTML")
        return
    
    from database import get_deal_messages, can_access_chat, get_deal
    
    # Проверяем доступ
    if not await can_access_chat(message.from_user.id, deal_id):
        await message.answer("❌ <b>Нет доступа к чату этой сделки</b>", parse_mode="HTML")
        return
    
    # Получаем информацию о сделке
    d = await get_deal(deal_id)
    if not d:
        await message.answer("❌ <b>Сделка не найдена</b>", parse_mode="HTML")
        return
    
    # Устанавливаем состояние для отправки сообщений администратором
    await state.set_state(AdminChatStates.WAIT_ADMIN_MESSAGE)
    await state.update_data(deal_id=deal_id)
    
    # Получаем сообщения
    messages = await get_deal_messages(deal_id, limit=50)
    
    # Форматируем историю
    from utils import format_chat_message
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    if not messages:
        text = f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📭 <b>Чат пуст. Напишите первое сообщение!</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin:back")]
        ])
    else:
        lines = [f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📋 <b>Последние сообщения:</b>\n"]
        for msg in messages[-20:]:  # Последние 20 сообщений
            lines.append(format_chat_message(msg))
        text = "\n".join(lines)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:chat:refresh:{deal_id}")],
            [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin:back")]
        ])
    
    # Отправляем порциями если слишком длинно
    if len(text) > 4000:
        # Делим на части
        text_parts = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) > 4000:
                text_parts.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current:
            text_parts.append(current)
        
        for i, part in enumerate(text_parts):
            if i == len(text_parts) - 1:
                # Последняя часть с кнопками
                await message.answer(part, reply_markup=kb, parse_mode="HTML")
            else:
                await message.answer(part, parse_mode="HTML")
    else:
        await message.answer(
            f"{text}\n\n"
            "✨ <b>Вы подключены к чату как администратор</b>\n"
            "💬 <b>Просто отправьте текст сообщения, и оно будет добавлено в чат</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )


# ==================== ОТПРАВКА СООБЩЕНИЙ АДМИНИСТРАТОРОМ ====================

@router.message(AdminChatStates.WAIT_ADMIN_MESSAGE)
async def admin_chat_message(message: Message, state: FSMContext):
    """Обработка сообщений администратора в чате сделки"""
    if not await admin_filter(message):
        await state.clear()
        return
    
    data = await state.get_data()
    deal_id = data.get("deal_id")
    
    if not deal_id:
        await state.clear()
        await message.answer("❌ <b>Сессия истекла. Используйте команду /chat_n снова.</b>", parse_mode="HTML")
        return
    
    # Проверяем доступ
    if not await can_access_chat(message.from_user.id, deal_id):
        await state.clear()
        await message.answer("❌ <b>Нет доступа к чату этой сделки</b>", parse_mode="HTML")
        return
    
    if not message.text:
        await message.answer("❌ <b>Напишите сообщение текстом</b>", parse_mode="HTML")
        return

    text = message.text.strip()
    
    if not text:
        await message.answer("❌ <b>Сообщение не может быть пустым</b>", parse_mode="HTML")
        return
    
    # Пропускаем команды (они обрабатываются отдельными обработчиками)
    if text.startswith("/"):
        return
    
    # Добавляем сообщение в чат
    try:
        # Добавляем префикс для администратора (без HTML, так как будет отформатировано при отображении)
        admin_text = f"👨‍⚖️ Администратор: {text}"
        await add_deal_message(deal_id, message.from_user.id, admin_text)
        
        await message.answer(
            f"✅ <b>Сообщение отправлено в чат сделки #{deal_id}</b>\n\n"
            f"💬 <b>Текст:</b> {text}\n\n"
            f"💡 <b>Для выхода используйте:</b> <code>/exit</code>",
            parse_mode="HTML"
        )
        
        # Уведомляем участников сделки
        d = await get_deal(deal_id)
        if d:
            participants = []
            if d.get("owner_id"):
                participants.append(d["owner_id"])
            if d.get("partner_id") and d["partner_id"] not in participants:
                participants.append(d["partner_id"])
            
            notification = (
                f"👨‍⚖️ <b>Сообщение от администратора</b>\n\n"
                f"Сделка #{deal_id}\n\n"
                f"{text}"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")]
            ])
            
            for user_id in participants:
                if user_id != message.from_user.id:
                    try:
                        await send_notification(message.bot, user_id, notification, reply_markup=kb)
                    except Exception:
                        pass
        
    except Exception as e:
        await message.answer(f"❌ <b>Ошибка отправки сообщения:</b> {e}", parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:chat:refresh:"))
async def admin_chat_refresh(callback: CallbackQuery, state: FSMContext):
    """Обновление просмотра чата администратором"""
    if not await admin_filter(callback):
        return
    
    deal_id = int(callback.data.split(":")[3])
    
    # Проверяем доступ
    if not await can_access_chat(callback.from_user.id, deal_id):
        await callback.answer("❌ Нет доступа к чату этой сделки", show_alert=True)
        return
    
    # Получаем сообщения
    messages = await get_deal_messages(deal_id, limit=50)
    
    # Форматируем историю
    if not messages:
        text = f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📭 <b>Чат пуст.</b>"
    else:
        lines = [f"✨ 💬 <b>Чат сделки #{deal_id}</b> 💬 ✨\n\n📋 <b>Последние сообщения:</b>\n"]
        for msg in messages[-20:]:
            lines.append(format_chat_message(msg))
        text = "\n".join(lines)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:chat:refresh:{deal_id}")],
        [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin:back")]
    ])
    
    try:
        await callback.message.edit_text(
            f"{text}\n\n"
            "✨ <b>Вы подключены к чату как администратор</b>\n"
            "💬 <b>Просто отправьте текст сообщения, и оно будет добавлено в чат</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            f"{text}\n\n"
            "✨ <b>Вы подключены к чату как администратор</b>\n"
            "💬 <b>Просто отправьте текст сообщения, и оно будет добавлено в чат</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    
    # Устанавливаем состояние
    await state.set_state(AdminChatStates.WAIT_ADMIN_MESSAGE)
    await state.update_data(deal_id=deal_id)
    
    await callback.answer("🔄 Обновлено")


@router.message(Command("exit"))
async def cmd_exit_admin_chat(message: Message, state: FSMContext):
    """Выход из режима отправки сообщений в чат"""
    if not await admin_filter(message):
        return
    
    data = await state.get_data()
    deal_id = data.get("deal_id")
    
    if deal_id:
        await state.clear()
        await message.answer(
            f"✅ <b>Вы вышли из режима отправки сообщений</b>\n\n"
            f"💡 <b>Используйте</b> <code>/chat_{deal_id}</code> <b>для повторного подключения</b>",
            parse_mode="HTML"
        )
    else:
        await state.clear()
        await message.answer("✅ <b>Режим отправки сообщений отключён</b>", parse_mode="HTML")


@router.message(Command("update_stats"))
async def cmd_update_stats(message: Message):
    """Команда /update_stats - принудительное обновление статистики"""
    if not await admin_filter(message):
        return
    
    await update_daily_stats()
    stats = await get_daily_stats()
    
    await message.answer(
        f"✅ Статистика обновлена\n\n"
        f"Дата: {stats['date']}\n"
        f"Всего сделок: {stats['total_deals']}\n"
        f"Успешных: {stats['successful_deals']}\n"
        f"Отменено: {stats['cancelled_deals']}\n"
        f"Споров: {stats['disputed_deals']}\n"
        f"Объём: {stats['total_volume_btc']} BTC\n"
        f"Комиссия: {stats['total_commission_btc']} BTC"
    )
