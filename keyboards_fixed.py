"""
Клавиатуры для бота
"""
from datetime import datetime
from decimal import Decimal

from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)


# ==================== ГЛАВНОЕ МЕНЮ ====================

def main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню в чате"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📈 Купить", callback_data="menu:buy"),
            InlineKeyboardButton(text="📉 Продать", callback_data="menu:sell")
        ],
        [InlineKeyboardButton(text="⚡️ Создать объявление", callback_data="menu:create")],
        [InlineKeyboardButton(text="🗳 Мои сделки", callback_data="menu:deals")],
        [
            InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help"),
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile")
        ]
    ])


# ==================== INLINE КЛАВИАТУРЫ ====================

def sell_or_buy_kb() -> InlineKeyboardMarkup:
    """Продать или купить"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📉 Продать", callback_data="sell:start"),
         InlineKeyboardButton(text="📈 Купить", callback_data="buy:create")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])


def active_kb() -> InlineKeyboardMarkup:
    """Активные заявки (продажа/покупка)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📉 продажа", callback_data="active:sell"),
         InlineKeyboardButton(text="📈 покупка", callback_data="active:buy")]
    ])


def my_deals_kb() -> InlineKeyboardMarkup:
    """Мои сделки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱️ Активные", callback_data="mydeals:active"),
         InlineKeyboardButton(text="📂 История", callback_data="mydeals:history")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])


def list_keyboard(deals: list[dict]) -> InlineKeyboardMarkup:
    """Список заявок (для просмотра публичных заявок)"""
    rows = []
    for d in deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        rng = f"{min_btc}—{max_btc}" if min_btc != max_btc else f"{max_btc}"
        t = datetime.fromisoformat(d["expires_at"]).strftime("%H:%M")
        label = f"{rng} BTC • до {t}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"deal:view:{d['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_list_keyboard(deals: list[dict], page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Список завершенных сделок с пагинацией
    
    Args:
        deals: Список сделок для текущей страницы
        page: Текущая страница (начинается с 1)
        total_pages: Общее количество страниц
    """
    rows = []
    
    # Добавляем кнопки сделок (максимум 8 на странице)
    for d in deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        buy_amount = d.get("buy_amount")
        status = d.get("status", "")
        
        # Определяем сумму для отображения
        if buy_amount:
            amount_btc = Decimal(buy_amount)
            rng = f"{amount_btc:.5f}"
        else:
            rng = f"{min_btc:.5f}—{max_btc:.5f}" if min_btc != max_btc else f"{max_btc:.5f}"
        
        # Добавляем эмодзи статуса
        status_emoji = ""
        if status == "Released":
            status_emoji = "✅ "
        elif status == "Cancelled":
            status_emoji = "❌ "
        elif status == "Disputed":
            status_emoji = "⚠️ "
        
        # Форматируем дату создания
        created = datetime.fromisoformat(d["created_at"]).strftime("%d.%m %H:%M")
        
        label = f"{status_emoji}#{d['id']} • {rng} BTC • {created}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"mydeal:view:{d['id']}")])
    
    # Добавляем кнопки навигации
    nav_buttons = []
    
    # Кнопка "Назад" (предыдущая страница)
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"mydeals:history:page:{page - 1}"))
    
    # Информация о странице
    nav_buttons.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="mydeals:history:info"))
    
    # Кнопка "Вперед" (следующая страница)
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"mydeals:history:page:{page + 1}"))
    
    if nav_buttons:
        rows.append(nav_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def my_deals_list_keyboard(deals: list[dict]) -> InlineKeyboardMarkup:
    """Список активных сделок пользователя (использует mydeal:view) с суммами в рублях"""
    from utils import get_btc_rub_rate, btc_to_rub
    from database import calculate_buyer_amounts
    
    # Получаем текущий курс для расчетов (если нужен)
    btc_rub_rate = await get_btc_rub_rate()
    
    rows = []
    for d in deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        buy_amount = d.get("buy_amount")
        status = d.get("status", "")
        
        # Определяем сумму в BTC для отображения
        if buy_amount:
            amount_btc = Decimal(buy_amount)
            display_btc = amount_btc
        else:
            display_btc = max_btc
            amount_btc = max_btc
        
        # Форматируем BTC сумму
        if buy_amount:
            rng = f"{amount_btc}"
        else:
            rng = f"{min_btc}—{max_btc}" if min_btc != max_btc else f"{max_btc}"
        
        # Рассчитываем сумму в рублях
        # Для MATCHED и PAYMENT_PENDING используем закрепленную сумму
        # Для остальных - текущий курс
        if status in ["Matched", "PaymentPending"] and d.get("locked_rub_amount"):
            # Используем закрепленную сумму
            rub_amount = Decimal(d["locked_rub_amount"])
        else:
            # Рассчитываем по текущему курсу
            buyer_amounts = calculate_buyer_amounts(display_btc)
            buyer_payment_btc = buyer_amounts['total_fiat_equivalent']
            rub_amount = btc_to_rub(buyer_payment_btc, btc_rub_rate)
        
        rub_formatted = f"{rub_amount:,.0f}".replace(",", " ")
        
        # Добавляем информацию о статусе
        status_emoji = ""
        if status == "Matched":
            status_emoji = "🤝 "
        elif status == "PaymentPending":
            status_emoji = "💳 "
        elif status == "AwaitingDeposit":
            status_emoji = "⏳ "
        elif status == "Escrowed":
            status_emoji = "✅ "
        
        t = datetime.fromisoformat(d["expires_at"]).strftime("%H:%M")
        label = f"{status_emoji}{rng} BTC ({rub_formatted} руб) • до {t}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"mydeal:view:{d['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_or_back_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Начать сделку или назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать сделку", callback_data=f"deal:start:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])


def paid_or_cancel_kb(deal_id: int, deal: dict = None) -> InlineKeyboardMarkup:
    """Оплатил / Открыть чат / Диспут (без отмены, так как сделка уже началась)"""
    buttons = []
    buttons.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
    
    # Показываем кнопку "Оплатил" только если оплата еще не отмечена
    if not deal or not deal.get("payment_marked_at"):
        buttons.append([InlineKeyboardButton(text="✅ Оплатил", callback_data=f"deal:paid:{deal_id}")])
    
    buttons.append([InlineKeyboardButton(text="⚠️ ОТКРЫТЬ ДИСПУТ", callback_data=f"deal:dispute:{deal_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def seller_verify_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Продавец проверяет оплату"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
        [InlineKeyboardButton(text="✅ Успешно", callback_data=f"seller:ok:{deal_id}")],
        [InlineKeyboardButton(text="💣 ОТКРЫТЬ ДИСПУТ", callback_data=f"deal:dispute:{deal_id}")]
    ])


def admin_deposit_review_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Админ проверяет депозит"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить депозит", callback_data=f"admin:confirm:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin:cancel:{deal_id}")]
    ])


def admin_release_review_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Админ делает релиз"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Релиз BTC", callback_data=f"admin:release:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin:cancel:{deal_id}")],
        [InlineKeyboardButton(text="⚠️ Спор", callback_data=f"admin:dispute:{deal_id}")]
    ])


def rate_partner_kb(deal_id: int, partner_id: int) -> InlineKeyboardMarkup:
    """Оценить контрагента"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐️ Оценить контрагента", callback_data=f"rate:start:{deal_id}:{partner_id}")],
        [InlineKeyboardButton(text="❤️ Добавить в избранное", callback_data=f"favorite:add:{partner_id}:{deal_id}")],
        [InlineKeyboardButton(text="Пропустить", callback_data=f"rate:skip:{deal_id}")]
    ])


def stars_kb(deal_id: int, partner_id: int) -> InlineKeyboardMarkup:
    """Выбор звёзд рейтинга"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐️", callback_data=f"rate:stars:1:{deal_id}:{partner_id}"),
            InlineKeyboardButton(text="⭐️⭐️", callback_data=f"rate:stars:2:{deal_id}:{partner_id}"),
            InlineKeyboardButton(text="⭐️⭐️⭐️", callback_data=f"rate:stars:3:{deal_id}:{partner_id}"),
        ],
        [
            InlineKeyboardButton(text="⭐️⭐️⭐️⭐️", callback_data=f"rate:stars:4:{deal_id}:{partner_id}"),
            InlineKeyboardButton(text="⭐️⭐️⭐️⭐️⭐️", callback_data=f"rate:stars:5:{deal_id}:{partner_id}"),
        ],
        [InlineKeyboardButton(text="Отмена", callback_data=f"rate:skip:{deal_id}")]
    ])


def speed_kb(deal_id: int, partner_id: int, stars: int) -> InlineKeyboardMarkup:
    """Оценка скорости"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🐌 Медленно", callback_data=f"rate:speed:1:{deal_id}:{partner_id}:{stars}"),
            InlineKeyboardButton(text="🚶 Нормально", callback_data=f"rate:speed:3:{deal_id}:{partner_id}:{stars}"),
            InlineKeyboardButton(text="⚡️ Быстро", callback_data=f"rate:speed:5:{deal_id}:{partner_id}:{stars}"),
        ],
        [InlineKeyboardButton(text="Пропустить комментарий", callback_data=f"rate:comment:skip:{deal_id}:{partner_id}:{stars}:3")]
    ])


def chat_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Клавиатура чата"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Написать сообщение", callback_data=f"chat:write:{deal_id}")],
        [InlineKeyboardButton(text="🔄 Обновить чат", callback_data=f"chat:refresh:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Назад к сделке", callback_data=f"mydeal:view:{deal_id}")]
    ])


def quick_replies_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Быстрые ответы в чате"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтверждаю", callback_data=f"chat:quick:confirm:{deal_id}")],
        [InlineKeyboardButton(text="⏰ Нужно время", callback_data=f"chat:quick:time:{deal_id}")],
        [InlineKeyboardButton(text="❓ Уточните детали", callback_data=f"chat:quick:details:{deal_id}")],
        [InlineKeyboardButton(text="✏️ Написать своё", callback_data=f"chat:write:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"chat:view:{deal_id}")]
    ])


def templates_kb() -> InlineKeyboardMarkup:
    """Шаблоны"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Мои реквизиты", callback_data="templates:payment")],
        [InlineKeyboardButton(text="⭐️ Избранные контрагенты", callback_data="templates:favorites")]
    ])


def saved_details_kb(saved_details: list[dict], deal_id: int = None) -> InlineKeyboardMarkup:
    """Список сохранённых реквизитов"""
    rows = []
    
    if deal_id:
        rows.append([InlineKeyboardButton(text="✏️ Ввести новые реквизиты", callback_data=f"sell:input_new:{deal_id}")])
    
    for detail in saved_details:
        rows.append([
            InlineKeyboardButton(text=f"📋 {detail['title']}", callback_data=f"sell:use_saved:{deal_id}:{detail['id']}" if deal_id else f"detail:view:{detail['id']}")
        ])
    
    if not deal_id:
        rows.append([InlineKeyboardButton(text="➕ Добавить новые", callback_data="templates:add_payment")])
    
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="templates:menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def favorites_kb(favorites: list[dict]) -> InlineKeyboardMarkup:
    """Список избранных контрагентов"""
    rows = []
    
    for fav in favorites:
        display_name = fav.get("display_name")
        if display_name:
            user_display = display_name
        else:
            user_display = f"ID {fav['partner_id']}"
        rating = fav.get("average_rating") or 0
        rating_str = "⭐️" * int(rating) if rating > 0 else "Нет оценок"
        label = f"{user_display} | {rating_str}"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"favorite:view:{fav['partner_id']}")
        ])
    
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="templates:menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_main_kb() -> InlineKeyboardMarkup:
    """Админ панель"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="⚠️ Споры", callback_data="admin:disputes")],
        [InlineKeyboardButton(text="⚠️ Проблемные сделки", callback_data="admin:problems")],
        [InlineKeyboardButton(text="💰 Активные депозиты", callback_data="admin:active_deposits")],
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin:users")],
    ])


def deal_actions_kb(deal: dict, user_id: int, is_owner: bool, saved_payment_details: list = None) -> InlineKeyboardMarkup:
    """Меню действий для сделки в зависимости от статуса и роли пользователя"""
    buttons = []
    status = deal.get("status")
    has_payment_details = bool(deal.get("payment_details"))
    
    # Получаем deal_id один раз в начале функции - пробуем разные способы
    deal_id = None
    try:
        if 'id' in deal:
            deal_id = int(deal['id'])
        elif hasattr(deal, 'get'):
            deal_id_val = deal.get('id')
            if deal_id_val is not None:
                deal_id = int(deal_id_val)
        elif hasattr(deal, '__getitem__'):
            try:
                deal_id = int(deal['id'])
            except (KeyError, ValueError, TypeError):
                pass
    except (ValueError, TypeError, KeyError, AttributeError):
        deal_id = None
    
    # Если deal_id не найден, пробуем получить через keys() если это Row объект
    if deal_id is None and hasattr(deal, 'keys'):
        try:
            for key in deal.keys():
                if key.lower() == 'id':
                    deal_id = int(deal[key])
                    break
        except:
            pass
    
    # Определяем роль
    role = "owner" if is_owner else "partner"
    
    if not deal_id:
        # Если deal_id не найден, логируем и возвращаем только кнопку "Назад"
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[KEYBOARDS] Не удалось получить deal_id из deal. Keys: {deal.keys() if hasattr(deal, 'keys') else 'N/A'}, Type: {type(deal)}")
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:active")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    if status == "AwaitingDeposit" and is_owner:
        # Владелец может подтвердить депозит или отменить сделку
        buttons.append([InlineKeyboardButton(text="✅ Депозит внесён", callback_data=f"sell:deposit_confirmed:{deal_id}")])
        buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"deal:cancel:{deal_id}")])
    
    elif status in ["Confirming", "Escrowed"] and is_owner:
        # Владелец может указать/изменить реквизиты
        if not has_payment_details:
            # Реквизиты не указаны - показать кнопку для ввода
            if saved_payment_details:
                buttons.append([InlineKeyboardButton(text="💳 Указать реквизиты", callback_data=f"deal:actions:payment:{deal_id}")])
                # Показываем до 3 сохраненных реквизитов прямо в меню
                for detail in saved_payment_details[:3]:
                    buttons.append([InlineKeyboardButton(text=f"📋 {detail['title']}", callback_data=f"sell:use_payment:{deal_id}:{detail['id']}")])
            else:
                buttons.append([InlineKeyboardButton(text="💳 Указать реквизиты", callback_data=f"sell:enter_new_payment:{deal_id}")])
        else:
            # Реквизиты указаны - можно изменить
            buttons.append([InlineKeyboardButton(text="✏️ Изменить реквизиты", callback_data=f"deal:actions:payment:{deal_id}")])
        buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"deal:cancel:{deal_id}")])
    
    elif status == "Active" and is_owner:
        # Активная заявка - можно отменить
        buttons.append([InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"deal:cancel:{deal_id}")])
    
    elif status == "Matched":
        # Сделка началась - действия для обеих сторон (отмена только через диспут)
        if deal.get("partner_id"):
            buttons.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
        
        if is_owner and deal.get("side") == "SELL" and has_payment_details:
            # Продавец видит реквизиты (они уже указаны)
            pass
        elif not is_owner:
            # Покупатель может оплатить (только если оплата еще не отмечена)
            if not deal.get("payment_marked_at"):
                buttons.append([InlineKeyboardButton(text="✅ Оплатил", callback_data=f"deal:paid:{deal_id}")])
        
        buttons.append([InlineKeyboardButton(text="⚠️ Открыть спор (отмена через администратора)", callback_data=f"deal:dispute:{deal_id}")])
    
    elif status == "PaymentPending":
        # Ожидание оплаты (отмена только через диспут)
        if deal.get("partner_id"):
            buttons.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
        
        if is_owner and deal.get("side") == "SELL":
            # Продавец проверяет оплату
            buttons.append([InlineKeyboardButton(text="✅ Оплата получена", callback_data=f"seller:ok:{deal_id}")])
        elif not is_owner:
            # Покупатель может повторить подтверждение оплаты (только если оплата еще не отмечена)
            if not deal.get("payment_marked_at"):
                buttons.append([InlineKeyboardButton(text="✅ Оплатил", callback_data=f"deal:paid:{deal_id}")])
        
        buttons.append([InlineKeyboardButton(text="⚠️ Открыть спор (отмена через администратора)", callback_data=f"deal:dispute:{deal_id}")])
    
    elif status == "Disputed":
        # Спор открыт
        if deal.get("partner_id"):
            buttons.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
        buttons.append([InlineKeyboardButton(text="ℹ️ Ожидание администратора", callback_data=f"deal:info:{deal_id}")])
    
    elif status == "Released":
        # Сделка завершена
        partner_id = deal.get("partner_id") or deal.get("buyer_id")
        if partner_id:
            buttons.append([InlineKeyboardButton(text="⭐️ Оценить контрагента", callback_data=f"rate:start:{deal_id}:{partner_id}")])
    
    # Кнопка "Действия" для сделок с дополнительными действиями
    if status in ["Confirming", "Escrowed"] and is_owner and has_payment_details:
        # Если реквизиты уже указаны, добавляем кнопку "Действия" в начало
        buttons.insert(0, [InlineKeyboardButton(text="⚙️ Действия", callback_data=f"deal:actions:{deal_id}")])
    
    # Всегда добавляем кнопку "Назад" в конец
    if status in ["AwaitingDeposit", "Confirming", "Escrowed", "Active", "Matched", "PaymentPending"]:
        back_to = "mydeals:active"
    else:
        back_to = "mydeals:history"
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_to)])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)





"""
Клавиатуры для бота
"""
from datetime import datetime
from decimal import Decimal

from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)


# ==================== ГЛАВНОЕ МЕНЮ ====================

def main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню в чате"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📈 Купить", callback_data="menu:buy"),
            InlineKeyboardButton(text="📉 Продать", callback_data="menu:sell")
        ],
        [InlineKeyboardButton(text="⚡️ Создать объявление", callback_data="menu:create")],
        [InlineKeyboardButton(text="🗳 Мои сделки", callback_data="menu:deals")],
        [
            InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help"),
            InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile")
        ]
    ])


# ==================== INLINE КЛАВИАТУРЫ ====================

def sell_or_buy_kb() -> InlineKeyboardMarkup:
    """Продать или купить"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📉 Продать", callback_data="sell:start"),
         InlineKeyboardButton(text="📈 Купить", callback_data="buy:create")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])


def active_kb() -> InlineKeyboardMarkup:
    """Активные заявки (продажа/покупка)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📉 продажа", callback_data="active:sell"),
         InlineKeyboardButton(text="📈 покупка", callback_data="active:buy")]
    ])


def my_deals_kb() -> InlineKeyboardMarkup:
    """Мои сделки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱️ Активные", callback_data="mydeals:active"),
         InlineKeyboardButton(text="📂 История", callback_data="mydeals:history")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])


def list_keyboard(deals: list[dict]) -> InlineKeyboardMarkup:
    """Список заявок (для просмотра публичных заявок)"""
    rows = []
    for d in deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        rng = f"{min_btc}—{max_btc}" if min_btc != max_btc else f"{max_btc}"
        t = datetime.fromisoformat(d["expires_at"]).strftime("%H:%M")
        label = f"{rng} BTC • до {t}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"deal:view:{d['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_list_keyboard(deals: list[dict], page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Список завершенных сделок с пагинацией
    
    Args:
        deals: Список сделок для текущей страницы
        page: Текущая страница (начинается с 1)
        total_pages: Общее количество страниц
    """
    rows = []
    
    # Добавляем кнопки сделок (максимум 8 на странице)
    for d in deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        buy_amount = d.get("buy_amount")
        status = d.get("status", "")
        
        # Определяем сумму для отображения
        if buy_amount:
            amount_btc = Decimal(buy_amount)
            rng = f"{amount_btc:.5f}"
        else:
            rng = f"{min_btc:.5f}—{max_btc:.5f}" if min_btc != max_btc else f"{max_btc:.5f}"
        
        # Добавляем эмодзи статуса
        status_emoji = ""
        if status == "Released":
            status_emoji = "✅ "
        elif status == "Cancelled":
            status_emoji = "❌ "
        elif status == "Disputed":
            status_emoji = "⚠️ "
        
        # Форматируем дату создания
        created = datetime.fromisoformat(d["created_at"]).strftime("%d.%m %H:%M")
        
        label = f"{status_emoji}#{d['id']} • {rng} BTC • {created}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"mydeal:view:{d['id']}")])
    
    # Добавляем кнопки навигации
    nav_buttons = []
    
    # Кнопка "Назад" (предыдущая страница)
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"mydeals:history:page:{page - 1}"))
    
    # Информация о странице
    nav_buttons.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="mydeals:history:info"))
    
    # Кнопка "Вперед" (следующая страница)
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"mydeals:history:page:{page + 1}"))
    
    if nav_buttons:
        rows.append(nav_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def my_deals_list_keyboard(deals: list[dict]) -> InlineKeyboardMarkup:
    """Список активных сделок пользователя (использует mydeal:view) с суммами в рублях"""
    from utils import get_btc_rub_rate, btc_to_rub
    from database import calculate_buyer_amounts
    
    # Получаем текущий курс для расчетов (если нужен)
    btc_rub_rate = await get_btc_rub_rate()
    
    rows = []
    for d in deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        buy_amount = d.get("buy_amount")
        status = d.get("status", "")
        
        # Определяем сумму в BTC для отображения
        if buy_amount:
            amount_btc = Decimal(buy_amount)
            display_btc = amount_btc
        else:
            display_btc = max_btc
            amount_btc = max_btc
        
        # Форматируем BTC сумму
        if buy_amount:
            rng = f"{amount_btc}"
        else:
            rng = f"{min_btc}—{max_btc}" if min_btc != max_btc else f"{max_btc}"
        
        # Рассчитываем сумму в рублях
        # Для MATCHED и PAYMENT_PENDING используем закрепленную сумму
        # Для остальных - текущий курс
        if status in ["Matched", "PaymentPending"] and d.get("locked_rub_amount"):
            # Используем закрепленную сумму
            rub_amount = Decimal(d["locked_rub_amount"])
        else:
            # Рассчитываем по текущему курсу
            buyer_amounts = calculate_buyer_amounts(display_btc)
            buyer_payment_btc = buyer_amounts['total_fiat_equivalent']
            rub_amount = btc_to_rub(buyer_payment_btc, btc_rub_rate)
        
        rub_formatted = f"{rub_amount:,.0f}".replace(",", " ")
        
        # Добавляем информацию о статусе
        status_emoji = ""
        if status == "Matched":
            status_emoji = "🤝 "
        elif status == "PaymentPending":
            status_emoji = "💳 "
        elif status == "AwaitingDeposit":
            status_emoji = "⏳ "
        elif status == "Escrowed":
            status_emoji = "✅ "
        
        t = datetime.fromisoformat(d["expires_at"]).strftime("%H:%M")
        label = f"{status_emoji}{rng} BTC ({rub_formatted} руб) • до {t}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"mydeal:view:{d['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_or_back_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Начать сделку или назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать сделку", callback_data=f"deal:start:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])


def paid_or_cancel_kb(deal_id: int, deal: dict = None) -> InlineKeyboardMarkup:
    """Оплатил / Открыть чат / Диспут (без отмены, так как сделка уже началась)"""
    buttons = []
    buttons.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
    
    # Показываем кнопку "Оплатил" только если оплата еще не отмечена
    if not deal or not deal.get("payment_marked_at"):
        buttons.append([InlineKeyboardButton(text="✅ Оплатил", callback_data=f"deal:paid:{deal_id}")])
    
    buttons.append([InlineKeyboardButton(text="⚠️ ОТКРЫТЬ ДИСПУТ", callback_data=f"deal:dispute:{deal_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def seller_verify_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Продавец проверяет оплату"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
        [InlineKeyboardButton(text="✅ Успешно", callback_data=f"seller:ok:{deal_id}")],
        [InlineKeyboardButton(text="💣 ОТКРЫТЬ ДИСПУТ", callback_data=f"deal:dispute:{deal_id}")]
    ])


def admin_deposit_review_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Админ проверяет депозит"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить депозит", callback_data=f"admin:confirm:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin:cancel:{deal_id}")]
    ])


def admin_release_review_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Админ делает релиз"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Релиз BTC", callback_data=f"admin:release:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin:cancel:{deal_id}")],
        [InlineKeyboardButton(text="⚠️ Спор", callback_data=f"admin:dispute:{deal_id}")]
    ])


def rate_partner_kb(deal_id: int, partner_id: int) -> InlineKeyboardMarkup:
    """Оценить контрагента"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐️ Оценить контрагента", callback_data=f"rate:start:{deal_id}:{partner_id}")],
        [InlineKeyboardButton(text="❤️ Добавить в избранное", callback_data=f"favorite:add:{partner_id}:{deal_id}")],
        [InlineKeyboardButton(text="Пропустить", callback_data=f"rate:skip:{deal_id}")]
    ])


def stars_kb(deal_id: int, partner_id: int) -> InlineKeyboardMarkup:
    """Выбор звёзд рейтинга"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐️", callback_data=f"rate:stars:1:{deal_id}:{partner_id}"),
            InlineKeyboardButton(text="⭐️⭐️", callback_data=f"rate:stars:2:{deal_id}:{partner_id}"),
            InlineKeyboardButton(text="⭐️⭐️⭐️", callback_data=f"rate:stars:3:{deal_id}:{partner_id}"),
        ],
        [
            InlineKeyboardButton(text="⭐️⭐️⭐️⭐️", callback_data=f"rate:stars:4:{deal_id}:{partner_id}"),
            InlineKeyboardButton(text="⭐️⭐️⭐️⭐️⭐️", callback_data=f"rate:stars:5:{deal_id}:{partner_id}"),
        ],
        [InlineKeyboardButton(text="Отмена", callback_data=f"rate:skip:{deal_id}")]
    ])


def speed_kb(deal_id: int, partner_id: int, stars: int) -> InlineKeyboardMarkup:
    """Оценка скорости"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🐌 Медленно", callback_data=f"rate:speed:1:{deal_id}:{partner_id}:{stars}"),
            InlineKeyboardButton(text="🚶 Нормально", callback_data=f"rate:speed:3:{deal_id}:{partner_id}:{stars}"),
            InlineKeyboardButton(text="⚡️ Быстро", callback_data=f"rate:speed:5:{deal_id}:{partner_id}:{stars}"),
        ],
        [InlineKeyboardButton(text="Пропустить комментарий", callback_data=f"rate:comment:skip:{deal_id}:{partner_id}:{stars}:3")]
    ])


def chat_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Клавиатура чата"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Написать сообщение", callback_data=f"chat:write:{deal_id}")],
        [InlineKeyboardButton(text="🔄 Обновить чат", callback_data=f"chat:refresh:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Назад к сделке", callback_data=f"mydeal:view:{deal_id}")]
    ])


def quick_replies_kb(deal_id: int) -> InlineKeyboardMarkup:
    """Быстрые ответы в чате"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтверждаю", callback_data=f"chat:quick:confirm:{deal_id}")],
        [InlineKeyboardButton(text="⏰ Нужно время", callback_data=f"chat:quick:time:{deal_id}")],
        [InlineKeyboardButton(text="❓ Уточните детали", callback_data=f"chat:quick:details:{deal_id}")],
        [InlineKeyboardButton(text="✏️ Написать своё", callback_data=f"chat:write:{deal_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"chat:view:{deal_id}")]
    ])


def templates_kb() -> InlineKeyboardMarkup:
    """Шаблоны"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Мои реквизиты", callback_data="templates:payment")],
        [InlineKeyboardButton(text="⭐️ Избранные контрагенты", callback_data="templates:favorites")]
    ])


def saved_details_kb(saved_details: list[dict], deal_id: int = None) -> InlineKeyboardMarkup:
    """Список сохранённых реквизитов"""
    rows = []
    
    if deal_id:
        rows.append([InlineKeyboardButton(text="✏️ Ввести новые реквизиты", callback_data=f"sell:input_new:{deal_id}")])
    
    for detail in saved_details:
        rows.append([
            InlineKeyboardButton(text=f"📋 {detail['title']}", callback_data=f"sell:use_saved:{deal_id}:{detail['id']}" if deal_id else f"detail:view:{detail['id']}")
        ])
    
    if not deal_id:
        rows.append([InlineKeyboardButton(text="➕ Добавить новые", callback_data="templates:add_payment")])
    
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="templates:menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def favorites_kb(favorites: list[dict]) -> InlineKeyboardMarkup:
    """Список избранных контрагентов"""
    rows = []
    
    for fav in favorites:
        display_name = fav.get("display_name")
        if display_name:
            user_display = display_name
        else:
            user_display = f"ID {fav['partner_id']}"
        rating = fav.get("average_rating") or 0
        rating_str = "⭐️" * int(rating) if rating > 0 else "Нет оценок"
        label = f"{user_display} | {rating_str}"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"favorite:view:{fav['partner_id']}")
        ])
    
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="templates:menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_main_kb() -> InlineKeyboardMarkup:
    """Админ панель"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="⚠️ Споры", callback_data="admin:disputes")],
        [InlineKeyboardButton(text="⚠️ Проблемные сделки", callback_data="admin:problems")],
        [InlineKeyboardButton(text="💰 Активные депозиты", callback_data="admin:active_deposits")],
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin:users")],
    ])


def deal_actions_kb(deal: dict, user_id: int, is_owner: bool, saved_payment_details: list = None) -> InlineKeyboardMarkup:
    """Меню действий для сделки в зависимости от статуса и роли пользователя"""
    buttons = []
    status = deal.get("status")
    has_payment_details = bool(deal.get("payment_details"))
    
    # Получаем deal_id один раз в начале функции - пробуем разные способы
    deal_id = None
    try:
        if 'id' in deal:
            deal_id = int(deal['id'])
        elif hasattr(deal, 'get'):
            deal_id_val = deal.get('id')
            if deal_id_val is not None:
                deal_id = int(deal_id_val)
        elif hasattr(deal, '__getitem__'):
            try:
                deal_id = int(deal['id'])
            except (KeyError, ValueError, TypeError):
                pass
    except (ValueError, TypeError, KeyError, AttributeError):
        deal_id = None
    
    # Если deal_id не найден, пробуем получить через keys() если это Row объект
    if deal_id is None and hasattr(deal, 'keys'):
        try:
            for key in deal.keys():
                if key.lower() == 'id':
                    deal_id = int(deal[key])
                    break
        except:
            pass
    
    # Определяем роль
    role = "owner" if is_owner else "partner"
    
    if not deal_id:
        # Если deal_id не найден, логируем и возвращаем только кнопку "Назад"
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[KEYBOARDS] Не удалось получить deal_id из deal. Keys: {deal.keys() if hasattr(deal, 'keys') else 'N/A'}, Type: {type(deal)}")
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:active")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    if status == "AwaitingDeposit" and is_owner:
        # Владелец может подтвердить депозит или отменить сделку
        buttons.append([InlineKeyboardButton(text="✅ Депозит внесён", callback_data=f"sell:deposit_confirmed:{deal_id}")])
        buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"deal:cancel:{deal_id}")])
    
    elif status in ["Confirming", "Escrowed"] and is_owner:
        # Владелец может указать/изменить реквизиты
        if not has_payment_details:
            # Реквизиты не указаны - показать кнопку для ввода
            if saved_payment_details:
                buttons.append([InlineKeyboardButton(text="💳 Указать реквизиты", callback_data=f"deal:actions:payment:{deal_id}")])
                # Показываем до 3 сохраненных реквизитов прямо в меню
                for detail in saved_payment_details[:3]:
                    buttons.append([InlineKeyboardButton(text=f"📋 {detail['title']}", callback_data=f"sell:use_payment:{deal_id}:{detail['id']}")])
            else:
                buttons.append([InlineKeyboardButton(text="💳 Указать реквизиты", callback_data=f"sell:enter_new_payment:{deal_id}")])
        else:
            # Реквизиты указаны - можно изменить
            buttons.append([InlineKeyboardButton(text="✏️ Изменить реквизиты", callback_data=f"deal:actions:payment:{deal_id}")])
        buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"deal:cancel:{deal_id}")])
    
    elif status == "Active" and is_owner:
        # Активная заявка - можно отменить
        buttons.append([InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"deal:cancel:{deal_id}")])
    
    elif status == "Matched":
        # Сделка началась - действия для обеих сторон (отмена только через диспут)
        if deal.get("partner_id"):
            buttons.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
        
        if is_owner and deal.get("side") == "SELL" and has_payment_details:
            # Продавец видит реквизиты (они уже указаны)
            pass
        elif not is_owner:
            # Покупатель может оплатить (только если оплата еще не отмечена)
            if not deal.get("payment_marked_at"):
                buttons.append([InlineKeyboardButton(text="✅ Оплатил", callback_data=f"deal:paid:{deal_id}")])
        
        buttons.append([InlineKeyboardButton(text="⚠️ Открыть спор (отмена через администратора)", callback_data=f"deal:dispute:{deal_id}")])
    
    elif status == "PaymentPending":
        # Ожидание оплаты (отмена только через диспут)
        if deal.get("partner_id"):
            buttons.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
        
        if is_owner and deal.get("side") == "SELL":
            # Продавец проверяет оплату
            buttons.append([InlineKeyboardButton(text="✅ Оплата получена", callback_data=f"seller:ok:{deal_id}")])
        elif not is_owner:
            # Покупатель может повторить подтверждение оплаты (только если оплата еще не отмечена)
            if not deal.get("payment_marked_at"):
                buttons.append([InlineKeyboardButton(text="✅ Оплатил", callback_data=f"deal:paid:{deal_id}")])
        
        buttons.append([InlineKeyboardButton(text="⚠️ Открыть спор (отмена через администратора)", callback_data=f"deal:dispute:{deal_id}")])
    
    elif status == "Disputed":
        # Спор открыт
        if deal.get("partner_id"):
            buttons.append([InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")])
        buttons.append([InlineKeyboardButton(text="ℹ️ Ожидание администратора", callback_data=f"deal:info:{deal_id}")])
    
    elif status == "Released":
        # Сделка завершена
        partner_id = deal.get("partner_id") or deal.get("buyer_id")
        if partner_id:
            buttons.append([InlineKeyboardButton(text="⭐️ Оценить контрагента", callback_data=f"rate:start:{deal_id}:{partner_id}")])
    
    # Кнопка "Действия" для сделок с дополнительными действиями
    if status in ["Confirming", "Escrowed"] and is_owner and has_payment_details:
        # Если реквизиты уже указаны, добавляем кнопку "Действия" в начало
        buttons.insert(0, [InlineKeyboardButton(text="⚙️ Действия", callback_data=f"deal:actions:{deal_id}")])
    
    # Всегда добавляем кнопку "Назад" в конец
    if status in ["AwaitingDeposit", "Confirming", "Escrowed", "Active", "Matched", "PaymentPending"]:
        back_to = "mydeals:active"
    else:
        back_to = "mydeals:history"
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_to)])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)






