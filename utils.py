"""
Вспомогательные функции
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import aiohttp
import logging
import time

logger = logging.getLogger(__name__)

# Кэш для курса BTC/RUB
_btc_rub_rate_cache = {
    "rate": None,
    "timestamp": 0,
    "cache_duration": 300  # 5 минут
}


async def get_btc_usd_rate() -> Decimal | None:
    """Получить актуальный курс BTC к USD с Binance
    
    Returns:
        Курс BTC/USD или None при ошибке
    """
    try:
        async with aiohttp.ClientSession() as session:
            # Получаем курс BTC/USDT с Binance
            async with session.get('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT', timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    btc_usdt_data = await resp.json()
                    btc_usdt_price = Decimal(btc_usdt_data['price'])
                    return btc_usdt_price
                else:
                    logger.warning(f"Не удалось получить курс BTC/USDT с Binance: статус {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"Ошибка при получении курса BTC/USD: {e}")
        return None


async def get_usd_rub_rate() -> Decimal | None:
    """Получить актуальный курс USD к рублям с API ЦБ РФ
    
    Returns:
        Курс USD/RUB или None при ошибке
    """
    try:
        async with aiohttp.ClientSession() as session:
            # Получаем курс USD/RUB с API ЦБ РФ
            async with session.get('https://www.cbr-xml-daily.ru/daily_json.js', timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    # API ЦБ возвращает JavaScript, но это валидный JSON
                    text = await resp.text()
                    import json
                    cbr_data = json.loads(text)
                    usd_rub_price = Decimal(str(cbr_data['Valute']['USD']['Value']))
                    return usd_rub_price
                else:
                    logger.warning(f"Не удалось получить курс USD/RUB с ЦБ РФ: статус {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"Ошибка при получении курса USD/RUB: {e}")
        return None


async def get_btc_rub_rate() -> Decimal:
    """Получить актуальный курс BTC к рублям с Binance
    
    Использует кэширование на 5 минут для уменьшения количества запросов.
    Если не удается получить курс, возвращает запасной вариант из конфига.
    """
    global _btc_rub_rate_cache
    
    # Проверяем кэш
    current_time = time.time()
    if (_btc_rub_rate_cache["rate"] is not None and 
        current_time - _btc_rub_rate_cache["timestamp"] < _btc_rub_rate_cache["cache_duration"]):
        return _btc_rub_rate_cache["rate"]
    
    try:
        # Получаем курс BTC/USD
        btc_usd_price = await get_btc_usd_rate()
        if btc_usd_price is None:
            return _get_fallback_rate()
        
        # Получаем курс USD/RUB
        usd_rub_price = await get_usd_rub_rate()
        if usd_rub_price is None:
            return _get_fallback_rate()
        
        # Рассчитываем курс BTC/RUB
        btc_rub_rate = btc_usd_price * usd_rub_price
        
        # Обновляем кэш
        _btc_rub_rate_cache["rate"] = btc_rub_rate
        _btc_rub_rate_cache["timestamp"] = current_time
        
        logger.info(f"Обновлен курс BTC/RUB: {btc_rub_rate:,.0f} ₽")
        return btc_rub_rate
            
    except Exception as e:
        logger.error(f"Неожиданная ошибка при получении курса: {e}")
        return _get_fallback_rate()


def _get_fallback_rate() -> Decimal:
    """Получить запасной курс из конфига"""
    from config import BTC_TO_RUB_RATE
    if _btc_rub_rate_cache["rate"] is None:
        _btc_rub_rate_cache["rate"] = BTC_TO_RUB_RATE
    return _btc_rub_rate_cache["rate"]


def format_datetime(iso_string: str) -> str:
    """Форматировать дату и время"""
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_string


def format_datetime_short(iso_string: str) -> str:
    """Форматировать дату и время в коротком формате (двузначный год)"""
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%d.%m.%y %H:%M")
    except Exception:
        return iso_string


def format_date(iso_string: str) -> str:
    """Форматировать только дату"""
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return iso_string


def format_time(iso_string: str) -> str:
    """Форматировать только время"""
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%H:%M")
    except Exception:
        return iso_string


def format_btc_amount(amount: str | Decimal) -> str:
    """Форматировать BTC сумму"""
    try:
        dec = Decimal(str(amount))
        return f"{dec:.8f}".rstrip('0').rstrip('.')
    except Exception:
        return str(amount)


def get_role_name(user_id: int, deal: dict) -> str:
    """Определить роль пользователя в сделке"""
    if (deal.get("side") or "").upper() == "GARANT":
        if user_id == deal["owner_id"]:
            return "Инициатор"
        return "Участник"
    if deal["side"] == "SELL":
        if user_id == deal["owner_id"]:
            return "Продавец"
        else:
            return "Покупатель"
    else:  # BUY
        if user_id == deal["owner_id"]:
            return "Покупатель"
        else:
            return "Продавец"


def get_status_emoji(status: str) -> str:
    """Получить эмодзи для статуса"""
    status_emojis = {
        "AwaitingDeposit": "⏳",
        "Confirming": "🔄",
        "Escrowed": "✅",
        "Active": "📢",
        "Matched": "🤝",
        "PaymentPending": "💳",
        "Released": "✅",
        "Cancelled": "❌",
        "Disputed": "⚠️"
    }
    return status_emojis.get(status, "❓")


def get_status_name(status: str) -> str:
    """Получить название статуса на русском"""
    status_names = {
        "AwaitingDeposit": "⏳ Ожидает депозита",
        "Confirming": "🔄 Подтверждение депозита",
        "Escrowed": "✅ Депозит подтверждён",
        "Active": "📢 Опубликована",
        "Matched": "🤝 Найден контрагент",
        "PaymentPending": "💳 Ожидает оплату",
        "Released": "✅ Успешно завершена",
        "Cancelled": "❌ Отменена",
        "Disputed": "⚠️ Спор"
    }
    return status_names.get(status, status)


def format_rating_stars(rating: float) -> str:
    """Форматировать рейтинг звёздами"""
    if rating <= 0:
        return "Нет оценок"
    stars = int(rating)
    return "⭐️" * stars


def format_speed_rating(seconds: int) -> tuple[str, str]:
    """Форматировать скорость ответа"""
    if seconds <= 0:
        return "❓", "Недостаточно данных"
    
    if seconds < 300:  # < 5 минут
        return "⚡️", "Очень быстро"
    elif seconds < 900:  # < 15 минут
        return "🚀", "Быстро"
    elif seconds < 1800:  # < 30 минут
        return "👍", "Нормально"
    else:
        return "🐌", "Медленно"


def truncate_text(text: str, max_length: int = 100) -> str:
    """Обрезать текст до указанной длины"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def escape_markdown(text: str) -> str:
    """Экранировать спецсимволы для Markdown"""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, '\\' + char)
    return text


def format_commission_summary(amounts: dict) -> str:
    """Краткое описание комиссии"""
    return (
        f"Депозит: {amounts['deposit']} BTC\n"
        f"Чистая комиссия: {amounts['seller_net_loss']} BTC (2.5%)\n"
        f"Вы получите: {amounts['net_to_seller']} BTC"
    )


def is_seller(user_id: int, deal: dict) -> bool:
    """Проверить является ли пользователь продавцом"""
    return (deal["side"] == "SELL" and user_id == deal["owner_id"]) or \
           (deal["side"] == "BUY" and user_id == deal.get("partner_id"))


def is_buyer(user_id: int, deal: dict) -> bool:
    """Проверить является ли пользователь покупателем"""
    return not is_seller(user_id, deal)


def calculate_time_left(expires_at: str) -> str:
    """Рассчитать оставшееся время"""
    try:
        exp_time = datetime.fromisoformat(expires_at)
        now = datetime.utcnow()
        delta = exp_time - now
        
        if delta.total_seconds() <= 0:
            return "Истекло"
        
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        
        if hours > 0:
            return f"{hours}ч {minutes}м"
        else:
            return f"{minutes}м"
    except Exception:
        return "Неизвестно"


async def format_profile_info(user: dict, stats: dict, show_username: bool = True, is_public: bool = False) -> str:
    """Форматировать информацию профиля
    
    Args:
        user: Данные пользователя
        stats: Статистика пользователя
        show_username: Показывать ли username (True для своего профиля, False для чужих)
        is_public: Является ли это публичным профилем (влияет на формат)
    """
    # Получаем данные
    avg_rating = stats.get("average_rating", 0.0)
    total_deals = stats.get('total_deals', 0)
    success_count = stats.get('success_count', 0)
    disputed_deals = stats.get('disputed_deals', 0)
    active_deals = stats.get('active_deals', 0)
    total_volume = stats.get('total_volume', '0')
    rating_count = stats.get('rating_count', 0)
    
    # Новый формат профиля
    lines = []
    
    # Заголовок
    lines.append("🦸Профиль")
    lines.append("")
    
    # Username и display_name
    username = user.get("username") or ""
    display_name = user.get("display_name") or ""
    
    if is_public:
        # Для публичного профиля показываем только display_name (без username)
        if display_name:
            lines.append(f"🦹🏼‍♂️{display_name}")
        else:
            lines.append(f"🦹🏼‍♂️ID {user['tg_id']}")
    else:
        # Для своего профиля показываем username и display_name
        if username and display_name:
            lines.append(f"🦹🏼‍♂️@{username} ({display_name})")
        elif username:
            lines.append(f"🦹🏼‍♂️@{username}")
        elif display_name:
            lines.append(f"🦹🏼‍♂️{display_name}")
        else:
            lines.append(f"🦹🏼‍♂️ID {user['tg_id']}")
    
    # Время по Москве
    try:
        import pytz
        moscow_tz = pytz.timezone('Europe/Moscow')
        moscow_time = datetime.now(moscow_tz)
        moscow_time_str = moscow_time.strftime("%d.%m.%Y %H:%M")
    except Exception as e:
        # Fallback если pytz не установлен или ошибка
        moscow_time_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    lines.append(f"└ {moscow_time_str}")
    lines.append("")
    
    # Статистика сделок
    lines.append("💰Статистика сделок:")
    lines.append("")
    
    if is_public:
        # Для публичного профиля: только число без процента
        lines.append(f"✅ Успешных: {success_count}")
    else:
        # Для своего профиля: с процентом
        if total_deals > 0:
            success_rate = (success_count / total_deals * 100) if total_deals > 0 else 0
            lines.append(f"✅ Успешных: {success_count} ({success_rate:.1f}%)")
        else:
            lines.append(f"✅ Успешных: 0 (0%)")
    
    # Активные сделки показываем только в своем профиле
    if not is_public:
        lines.append(f"🚀 Активных: {active_deals}")
    
    lines.append(f"💣 Спорных: {disputed_deals}")
    lines.append("")
    
    # Финансовая статистика
    total_volume_decimal = Decimal(total_volume) if isinstance(total_volume, str) else Decimal(str(total_volume))
    
    lines.append("💰 Финансовая статистика:")
    
    # Получаем курс BTC/RUB для конвертации
    btc_rub_rate = await get_btc_rub_rate()
    
    # Для своего профиля: проверяем депозиты по активным сделкам
    if not is_public:
        from database import get_user_active_deals
        from config import DealStatus
        active_deals_list = await get_user_active_deals(user['tg_id'])
        total_deposit = Decimal('0')
        
        # Статусы, где депозит уже внесен (исключаем AWAITING_DEPOSIT)
        deposited_statuses = [
            DealStatus.CONFIRMING,
            DealStatus.ESCROWED,
            DealStatus.ACTIVE,
            DealStatus.MATCHED,
            DealStatus.PAYMENT_PENDING
        ]
        
        for deal in active_deals_list:
            # Считаем депозит только для SELL сделок, где депозит уже внесен
            if deal.get("side") == "SELL" and deal.get("status") in deposited_statuses:
                deposit = Decimal(deal.get("max_btc", "0"))
                total_deposit += deposit
        
        # Показываем депозит только если он есть
        if total_deposit > 0:
            deposit_rub = btc_to_rub(total_deposit, btc_rub_rate)
            deposit_rub_formatted = f"{deposit_rub:,.0f}".replace(",", " ")
            lines.append(f"└ {total_deposit:.4f} BTC ({deposit_rub_formatted} руб)")
    
    lines.append("")
    
    total_volume_rub = btc_to_rub(total_volume_decimal, btc_rub_rate)
    
    # Форматируем сумму в рублях с пробелами для тысяч
    total_volume_rub_formatted = f"{total_volume_rub:,.0f}".replace(",", " ")
    
    lines.append(f"💎BTC={total_volume_decimal:.5f} BTC")
    lines.append(f"🪙RUB={total_volume_rub_formatted} руб")
    lines.append("")
    
    # Рейтинг
    if avg_rating > 0:
        lines.append(f"⭐️ Рейтинг: {avg_rating:.1f}/5.0")
    else:
        lines.append("⭐️ Рейтинг: Нет оценок")
    
    # Количество оценок
    if is_public:
        # Для публичного профиля: без жирного шрифта
        lines.append(f"💌 Оценок: {rating_count}")
    else:
        # Для своего профиля: жирным шрифтом
        lines.append(f"💌 <b>Оценок: {rating_count}</b>")
    
    return "\n".join(lines)


def validate_payment_details(details: str) -> bool:
    """Проверить корректность реквизитов"""
    if not details or len(details.strip()) < 5:
        return False
    if len(details) > 500:
        return False
    return True


def validate_comment(comment: str) -> bool:
    """Проверить корректность комментария"""
    if len(comment) > 500:
        return False
    return True


def format_deal_summary(deal: dict, user_id: int) -> str:
    """Краткое описание сделки"""
    role = get_role_name(user_id, deal)
    status = get_status_name(deal["status"])
    amount = deal.get("buy_amount") or f"{deal['min_btc']} - {deal['max_btc']}"
    created = format_datetime(deal["created_at"])
    
    lines = [
        f"📋 Сделка #{deal['id']}",
        f"Ваша роль: {role}",
        f"Сумма: {amount} BTC",
        f"Статус: {status}",
        f"Создана: {created}",
    ]
    
    return "\n".join(lines)


def format_chat_message(msg: dict) -> str:
    """Форматировать сообщение из чата"""
    username = msg.get("username") or "Система"
    time_str = format_time(msg["created_at"])
    
    if msg.get("is_system"):
        return f"[{time_str}] 🤖 {msg['message_text']}"
    else:
        # Assuming admin messages are stored with "👨‍⚖️ Администратор: " prefix
        if msg['message_text'].startswith("👨‍⚖️ Администратор:"):
            return f"[{time_str}] {msg['message_text']}"
        return f"[{time_str}] @{username}: {msg['message_text']}"


def format_notification_text(title: str, body: str, deal_id: int = None) -> str:
    """Форматировать текст уведомления"""
    text = f"<b>{title}</b>\n\n{body}"
    if deal_id:
        text += f"\n\nСделка #{deal_id}"
    return text


def get_time_until_expiry(expires_at: str) -> int:
    """Получить количество минут до истечения"""
    try:
        exp_time = datetime.fromisoformat(expires_at)
        now = datetime.utcnow()
        delta = exp_time - now
        return int(delta.total_seconds() / 60)
    except Exception:
        return 0


def _fmt_stat_num(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def format_admin_stats(stats: dict) -> str:
    """Форматировать статистику для админа"""
    stats = stats or {}
    return (
        f"📊 <b>Статистика за {stats.get('period_days', 7)} дней</b>\n\n"
        f"Всего сделок: {stats.get('total_deals', 0)}\n"
        f"Успешных: {stats.get('successful_deals', 0)}\n"
        f"Отменено: {stats.get('cancelled_deals', 0)}\n"
        f"Споров: {stats.get('disputed_deals', 0)}\n\n"
        f"💰 Объём: {_fmt_stat_num(stats.get('total_volume_btc')):.8f} BTC\n"
        f"💵 Комиссия: {_fmt_stat_num(stats.get('total_commission_btc')):.8f} BTC\n\n"
        f"👥 Средняя активность: {_fmt_stat_num(stats.get('avg_active_users')):.1f} пользователей/день\n"
        f"📈 Среднее кол-во сделок: {_fmt_stat_num(stats.get('avg_deals_per_day')):.1f} в день"
    )


async def safe_edit_message(message, text: str, reply_markup=None, parse_mode="HTML"):
    """Безопасное редактирование сообщения с обработкой ошибок"""
    from aiogram.exceptions import TelegramBadRequest
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        # Игнорируем ошибку "message is not modified"
        if "message is not modified" in str(e).lower():
            logger.debug(f"Сообщение не изменено (это нормально): {e}")
        else:
            logger.warning(f"Ошибка редактирования сообщения: {e}")
            # Пытаемся отправить новое сообщение
            try:
                await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception as e2:
                logger.error(f"Не удалось отправить новое сообщение: {e2}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при редактировании сообщения: {e}")
        try:
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e2:
            logger.error(f"Не удалось отправить новое сообщение: {e2}")


def validate_deal_id(deal_id: any) -> int | None:
    """Валидация ID сделки"""
    try:
        deal_id = int(deal_id)
        if deal_id > 0 and deal_id < 1000000:
            return deal_id
    except (ValueError, TypeError):
        pass
    return None


def validate_user_id(user_id: any) -> int | None:
    """Валидация ID пользователя"""
    try:
        user_id = int(user_id)
        if user_id > 0:
            return user_id
    except (ValueError, TypeError):
        pass
    return None


def validate_btc_amount(amount: str | Decimal) -> Decimal | None:
    """Валидация суммы BTC"""
    try:
        if isinstance(amount, str):
            amount = Decimal(amount)
        if amount > 0 and amount <= Decimal("21000000"):  # Максимум BTC в обращении
            return amount
    except (ValueError, InvalidOperation, TypeError):
        pass
    return None


def rub_to_btc(rub_amount: Decimal | str, btc_rate: Decimal) -> Decimal:
    """Конвертировать рубли в BTC
    
    Args:
        rub_amount: Сумма в рублях
        btc_rate: Курс BTC к рублям (сколько рублей за 1 BTC)
    
    Returns:
        Сумма в BTC
    """
    if isinstance(rub_amount, str):
        rub_amount = Decimal(rub_amount)
    if btc_rate <= 0:
        raise ValueError("Курс BTC должен быть больше нуля")
    return rub_amount / btc_rate


def btc_to_rub(btc_amount: Decimal | str, btc_rate: Decimal) -> Decimal:
    """Конвертировать BTC в рубли
    
    Args:
        btc_amount: Сумма в BTC
        btc_rate: Курс BTC к рублям (сколько рублей за 1 BTC)
    
    Returns:
        Сумма в рублях
    """
    if isinstance(btc_amount, str):
        btc_amount = Decimal(btc_amount)
    if btc_rate <= 0:
        raise ValueError("Курс BTC должен быть больше нуля")
    return btc_amount * btc_rate


def format_currency_amount(amount: Decimal | str, currency: str = "BTC") -> str:
    """Форматировать сумму в зависимости от валюты
    
    Args:
        amount: Сумма
        currency: Валюта ("BTC" или "RUB")
    
    Returns:
        Отформатированная строка
    """
    if isinstance(amount, str):
        amount = Decimal(amount)
    
    if currency == "BTC":
        return format_btc_amount(amount)
    elif currency == "RUB":
        return f"{amount:,.0f}".replace(",", " ") + " ₽"
    else:
        return str(amount)


def pyrogram_proxy_dict():
    """Прокси для Pyrogram (forwarder)."""
    from config import TELEGRAM_PROXY
    if not TELEGRAM_PROXY:
        return None
    from urllib.parse import urlparse
    p = urlparse(TELEGRAM_PROXY)
    scheme = (p.scheme or "http").lower()
    if scheme == "socks5h":
        scheme = "socks5"
    port = p.port or (1080 if scheme.startswith("socks") else 8080)
    if not p.hostname:
        return None
    return {"scheme": scheme, "hostname": p.hostname, "port": port}


def create_bot(token: str, **kwargs):
    """Создать aiogram Bot с прокси, если задан TELEGRAM_PROXY."""
    from aiogram import Bot
    from config import TELEGRAM_PROXY
    if TELEGRAM_PROXY:
        from aiogram.client.session.aiohttp import AiohttpSession
        session = AiohttpSession(proxy=TELEGRAM_PROXY)
        return Bot(token=token, session=session, **kwargs)
    return Bot(token=token, **kwargs)
