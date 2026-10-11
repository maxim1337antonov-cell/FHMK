"""

Работа с базой данных

"""

import re

import time

from datetime import datetime, timedelta

from decimal import Decimal, InvalidOperation, ROUND_DOWN, getcontext

from contextlib import asynccontextmanager

from collections import defaultdict



import aiosqlite

from aiogram import Bot



from config import (

    DB_PATH, TTL_HOURS, DealStatus,

    SELLER_COMMISSION_RATE, BUYER_EXTRA_RATE, CANCELLATION_FEE,

    MESSAGE_COOLDOWN, FORBIDDEN_WORDS, ADMIN_IDS, ADMIN_CHAT_ID, BOT_TOKEN

)



# Точность BTC до 8 знаков

getcontext().prec = 16



# Регулярные выражения

NUM_RE = re.compile(r"([0-9]+(?:\.[0-9]{1,8})?)")

BECH32_RE = re.compile(r"^(bc1)[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{25,87}$")



# Rate limiting для сообщений

user_last_message_time = defaultdict(float)





def cleanup_old_cooldowns():

    """Очистка старых записей о сообщениях (вызывать периодически)"""

    now = time.time()

    to_remove = [uid for uid, last_time in user_last_message_time.items() 

                 if now - last_time > 3600]  # Удаляем записи старше 1 часа

    for uid in to_remove:

        del user_last_message_time[uid]





# ==================== ВАЛИДАЦИЯ ====================



def is_valid_btc_bech32(addr: str) -> bool:

    """Проверка валидности BTC адреса"""
    
    if not addr or not isinstance(addr, str):
        return False
    
    # Проверяем, что адрес не содержит пробелов (до strip)
    if addr != addr.strip():
        return False
    
    return bool(BECH32_RE.match(addr.strip().lower()))





def parse_limits(text: str) -> tuple[Decimal, Decimal]:

    """Парсинг лимитов из текста"""

    if not isinstance(text, str) or not text.strip():

        raise ValueError("Нужно указать мин и макс, пример: 0.001 - 0.10")
    
    # Заменяем запятую на точку (для поддержки европейского формата)
    text_normalized = text.replace(",", ".").replace(" ", "").replace("\u00A0", "").strip()

    if text_normalized.startswith("-"):

        raise ValueError("Значения должны быть > 0")

    nums = NUM_RE.findall(text_normalized.lower())

    if len(nums) < 2:

        raise ValueError("Нужно указать мин и макс, пример: 0.001 - 0.10")

    try:

        min_btc = Decimal(nums[0])

        max_btc = Decimal(nums[1])

    except InvalidOperation:

        raise ValueError("Некорректные числа")

    if min_btc <= 0 or max_btc <= 0:

        raise ValueError("Значения должны быть > 0")

    if max_btc < min_btc:

        raise ValueError("Максимум не может быть меньше минимума")

    q = Decimal("0.00000001")

    return (min_btc.quantize(q, rounding=ROUND_DOWN), 

            max_btc.quantize(q, rounding=ROUND_DOWN))





# ==================== РАСЧЁТ КОМИССИЙ ====================



def calculate_seller_amounts(btc_amount: Decimal) -> dict:

    """

    Рассчитывает суммы для продавца

    НОВАЯ ЛОГИКА: Продавец не платит комиссию
    Продавец получает полную сумму, которую платит покупатель

    """

    # Валидация входных данных
    if btc_amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    
    q = Decimal("0.00000001")

    # Продавец получает полную сумму, которую платит покупатель
    # Комиссия удерживается только с покупателя (0% временно)
    platform_takes = Decimal("0")  # Продавец не платит комиссию
    buyer_compensates = Decimal("0")  # Больше нет компенсации
    seller_net_loss = Decimal("0")  # Продавец ничего не теряет
    
    # Продавец получает полную сумму, которую платит покупатель
    net_to_seller = btc_amount.quantize(q, rounding=ROUND_DOWN)

    return {

        "deposit": btc_amount,

        "platform_takes": platform_takes,

        "buyer_compensates": buyer_compensates,

        "seller_net_loss": seller_net_loss,

        "net_to_seller": net_to_seller,

    }





def calculate_buyer_amounts(btc_amount: Decimal) -> dict:

    """

    НОВАЯ ЛОГИКА: Покупатель платит базовую сумму продавцу
    Покупатель получает базовую сумму минус комиссию (удерживает бот)

    Пример: Покупатель хочет купить 100 руб, платит 100 руб продавцу, получает 100% в BTC (комиссия 0%)

    """

    # Валидация входных данных
    if btc_amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    
    q = Decimal("0.00000001")

    # Покупатель платит базовую сумму (без доплат)
    buyer_payment = btc_amount.quantize(q, rounding=ROUND_DOWN)
    
    # Покупатель получает базовую сумму минус комиссию (0% временно)
    from config import BUYER_COMMISSION_RATE
    commission = (btc_amount * BUYER_COMMISSION_RATE).quantize(q, rounding=ROUND_DOWN)
    buyer_receive = (btc_amount - commission).quantize(q, rounding=ROUND_DOWN)

    return {

        "base_btc": btc_amount,  # Базовая сумма покупки

        "buyer_payment": buyer_payment,  # Что платит покупатель (базовая сумма)

        "buyer_receive": buyer_receive,  # Что получает покупатель (базовая сумма - комиссия)

        "buyer_commission": commission,  # Комиссия платформы (0% временно)

        # Для обратной совместимости
        "buyer_extra": Decimal("0"),
        "total_fiat_equivalent": buyer_payment,

    }





def calculate_cancellation_refund(btc_amount: Decimal) -> dict:

    """

    При отмене сделки продавцом возвращается депозит минус 1.5% штраф

    """

    q = Decimal("0.00000001")

    

    cancellation_fee = (btc_amount * CANCELLATION_FEE).quantize(q, rounding=ROUND_DOWN)

    refund_amount = btc_amount - cancellation_fee

    

    return {

        "original_deposit": btc_amount,

        "cancellation_fee": cancellation_fee,

        "refund_amount": refund_amount,

    }





def format_commission_info_seller(amounts: dict) -> str:

    """Информация для продавца"""

    lines = [

        "💰 Расчёт комиссий:",

        f"Депозит: {amounts['deposit']} BTC",

        f"Платформа удержит: {amounts['platform_takes']} BTC (5%)",

        f"Из комиссии платформы покупатель доплачивает: эквивалент {amounts['buyer_compensates']} BTC в фиате (+2.5%)",

        f"Ваша чистая комиссия: {amounts['seller_net_loss']} BTC (2.5%)",

        f"Вы получите в фиат: эквивалент {amounts['net_to_seller']} BTC (97.5%)"

    ]

    return "\n".join(lines)





def format_commission_info_buyer(seller_amounts: dict, buyer_amounts: dict) -> str:

    """Информация для покупателя (НОВАЯ ЛОГИКА)"""

    lines = [

        "💰 Расчёт оплаты:",

        f"Вы платите: {buyer_amounts['buyer_payment']:.8f} BTC",

        f"Вы получите: {buyer_amounts['buyer_receive']:.8f} BTC (100% от суммы покупки)",

        f"Комиссия платформы: {buyer_amounts['buyer_commission']:.8f} BTC (0%)",

        "",

        f"Продавец получит: {seller_amounts['net_to_seller']:.8f} BTC (полная сумма, которую вы платите)",

        f"Платформа удержит: {buyer_amounts['buyer_commission']:.8f} BTC (0% с суммы покупки - временно)",

    ]

    return "\n".join(lines)





# ==================== УВЕДОМЛЕНИЯ ====================



async def send_notification(bot, user_id: int, text: str, reply_markup=None):

    """Отправка уведомления с обработкой ошибок"""

    try:

        await bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode="HTML")

    except Exception as e:

        print(f"Ошибка отправки уведомления пользователю {user_id}: {e}")


async def send_public_request_notification(deal_id: int, bot: Bot | None = None):
    """Опубликовать заявку в публичный канал/чат (если настроен)"""
    from config import PUBLIC_REQUESTS_CHAT_ID, BOT_TOKEN
    if not PUBLIC_REQUESTS_CHAT_ID:
        return
    import logging
    logger = logging.getLogger(__name__)
    bot_created = False
    try:
        deal = await get_deal(deal_id)
        if not deal:
            return

        owner = await get_user(deal["owner_id"])
        from database import get_user_statistics
        owner_stats = await get_user_statistics(deal["owner_id"]) if owner else None

        from decimal import Decimal
        min_btc = Decimal(str(deal["min_btc"]))
        max_btc = Decimal(str(deal["max_btc"]))
        if min_btc == max_btc:
            amount_text = f"{min_btc:.8f}".rstrip('0').rstrip('.') + " BTC"
        else:
            min_fmt = f"{min_btc:.8f}".rstrip('0').rstrip('.')
            max_fmt = f"{max_btc:.8f}".rstrip('0').rstrip('.')
            amount_text = f"{min_fmt} - {max_fmt} BTC"

        side = (deal.get("side") or "").upper()
        if side == "BUY":
            side_text = "Покупка BTC"
            rating_label = "Рейтинг покупателя"
        else:
            side_text = "Продажа BTC"
            rating_label = "Рейтинг продавца"

        from utils import get_btc_rub_rate, btc_to_rub, get_status_name
        rub_text = ""
        try:
            btc_rub_rate = await get_btc_rub_rate()
            rub_amount = btc_to_rub(max_btc, btc_rub_rate)
            rub_text = f" (≈ {rub_amount:,.0f} руб)".replace(",", " ")
        except Exception:
            rub_text = ""

        rating = 0.0
        if owner_stats:
            rating = float(owner_stats.get("average_rating") or 0.0)
        rating_text = f"{rating:.1f}/5.0" if rating > 0 else "Нет оценок"

        text = (
            "🪙 Новая заявка:\n\n"
            f"⚡️ Тип: {side_text}\n"
            f"💰 Сумма: {amount_text}{rub_text}\n"
            f"⭐️ {rating_label}: {rating_text}"
        )

        if bot is None:
            from utils import create_bot
            bot = create_bot(BOT_TOKEN)
            bot_created = True
        bot_username = None
        try:
            me = await bot.get_me()
            bot_username = me.username
        except Exception:
            bot_username = None

        reply_markup = None
        if bot_username:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            deep_link = f"https://t.me/{bot_username}?start=deal_{deal_id}"
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔎 Открыть заявку", url=deep_link)]]
            )

        await bot.send_message(
            PUBLIC_REQUESTS_CHAT_ID,
            text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Не удалось опубликовать заявку в публичный канал: {e}", exc_info=True)
    finally:
        if bot_created and bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass





# ==================== СТРУКТУРА БД ====================



INIT_SQL = r"""

PRAGMA journal_mode=WAL;



CREATE TABLE IF NOT EXISTS users (

  id INTEGER PRIMARY KEY AUTOINCREMENT,

  tg_id INTEGER UNIQUE NOT NULL,

  username TEXT,

  name TEXT,

  display_name TEXT,

  btc_wallet TEXT,

  success_count INTEGER NOT NULL DEFAULT 0,

  total_deals INTEGER NOT NULL DEFAULT 0,

  average_rating REAL DEFAULT 0.0,

  total_rating_points INTEGER DEFAULT 0,

  rating_count INTEGER DEFAULT 0,

  response_time_avg INTEGER DEFAULT 0,

  last_active TEXT,

  total_volume_btc TEXT DEFAULT '0',

  cancelled_deals INTEGER DEFAULT 0,

  disputed_deals INTEGER DEFAULT 0,

  total_commission_paid TEXT DEFAULT '0'

);



CREATE TABLE IF NOT EXISTS deals (

  id INTEGER PRIMARY KEY AUTOINCREMENT,

  owner_id INTEGER NOT NULL,

  side TEXT NOT NULL DEFAULT 'SELL',

  min_btc TEXT NOT NULL,

  max_btc TEXT NOT NULL,

  status TEXT NOT NULL,

  escrow_address TEXT NOT NULL,

  payment_details TEXT,

  buyer_wallet TEXT,

  partner_id INTEGER,

  buyer_id INTEGER,

  buy_amount TEXT,

  platform_commission TEXT,

  buyer_extra TEXT,

  seller_net_loss TEXT,

  net_to_seller TEXT,

  total_fiat_equivalent TEXT,

  matched_at TEXT,

  payment_marked_at TEXT,

  seller_confirmed_at TEXT,

  completed_at TEXT,

  created_at TEXT NOT NULL,

  expires_at TEXT NOT NULL

);



CREATE TABLE IF NOT EXISTS ratings (

  id INTEGER PRIMARY KEY AUTOINCREMENT,

  deal_id INTEGER NOT NULL,

  from_user_id INTEGER NOT NULL,

  to_user_id INTEGER NOT NULL,

  stars INTEGER NOT NULL CHECK(stars >= 1 AND stars <= 5),

  speed_rating INTEGER CHECK(speed_rating >= 1 AND speed_rating <= 5),

  comment TEXT,

  created_at TEXT NOT NULL,

  FOREIGN KEY(deal_id) REFERENCES deals(id),

  UNIQUE(deal_id, from_user_id)

);



CREATE TABLE IF NOT EXISTS daily_stats (

  id INTEGER PRIMARY KEY AUTOINCREMENT,

  date TEXT UNIQUE NOT NULL,

  total_deals INTEGER DEFAULT 0,

  successful_deals INTEGER DEFAULT 0,

  cancelled_deals INTEGER DEFAULT 0,

  disputed_deals INTEGER DEFAULT 0,

  total_volume_btc TEXT DEFAULT '0',

  total_commission_btc TEXT DEFAULT '0',

  active_users INTEGER DEFAULT 0,

  new_users INTEGER DEFAULT 0

);



CREATE TABLE IF NOT EXISTS saved_payment_details (

  id INTEGER PRIMARY KEY AUTOINCREMENT,

  user_id INTEGER NOT NULL,

  title TEXT NOT NULL,

  details TEXT NOT NULL,

  created_at TEXT NOT NULL,

  FOREIGN KEY(user_id) REFERENCES users(tg_id)

);



CREATE TABLE IF NOT EXISTS favorite_partners (

  id INTEGER PRIMARY KEY AUTOINCREMENT,

  user_id INTEGER NOT NULL,

  partner_id INTEGER NOT NULL,

  note TEXT,

  added_at TEXT NOT NULL,

  FOREIGN KEY(user_id) REFERENCES users(tg_id),

  FOREIGN KEY(partner_id) REFERENCES users(tg_id),

  UNIQUE(user_id, partner_id)

);



CREATE TABLE IF NOT EXISTS deal_messages (

  id INTEGER PRIMARY KEY AUTOINCREMENT,

  deal_id INTEGER NOT NULL,

  from_user_id INTEGER NOT NULL,

  message_text TEXT NOT NULL,

  created_at TEXT NOT NULL,

  is_system BOOLEAN DEFAULT 0,

  FOREIGN KEY(deal_id) REFERENCES deals(id),

  FOREIGN KEY(from_user_id) REFERENCES users(tg_id)

);



CREATE TABLE IF NOT EXISTS processed_deposit_msgs (

  chat_id INTEGER NOT NULL,

  message_id INTEGER NOT NULL,

  created_at TEXT NOT NULL,

  PRIMARY KEY (chat_id, message_id)

);



CREATE INDEX IF NOT EXISTS idx_saved_details_user ON saved_payment_details(user_id);

CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorite_partners(user_id);

CREATE INDEX IF NOT EXISTS idx_ratings_to_user ON ratings(to_user_id);

CREATE INDEX IF NOT EXISTS idx_ratings_deal ON ratings(deal_id);

CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);

CREATE INDEX IF NOT EXISTS idx_deals_created ON deals(created_at);

CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date);

CREATE INDEX IF NOT EXISTS idx_deal_messages_deal ON deal_messages(deal_id);

CREATE INDEX IF NOT EXISTS idx_deal_messages_created ON deal_messages(created_at);

CREATE INDEX IF NOT EXISTS idx_deals_owner_id ON deals(owner_id);

CREATE INDEX IF NOT EXISTS idx_deals_partner_id ON deals(partner_id);

CREATE INDEX IF NOT EXISTS idx_deals_expires_at ON deals(expires_at);

CREATE INDEX IF NOT EXISTS idx_deals_side_status ON deals(side, status);

"""





# ==================== КОНТЕКСТНЫЙ МЕНЕДЖЕР БД ====================



@asynccontextmanager

async def db():

    """Контекстный менеджер для работы с БД"""

    conn = await aiosqlite.connect(DB_PATH)

    conn.row_factory = aiosqlite.Row

    try:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA journal_mode=WAL")

        yield conn

    finally:

        await conn.close()





async def init_db():

    """Инициализация базы данных"""

    async with db() as conn:

        await conn.executescript(INIT_SQL)

        await conn.commit()

        # Добавляем новые поля в таблицу users, если их нет
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN name TEXT")
            await conn.commit()
        except Exception:
            pass  # Поле уже существует
        
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN btc_wallet TEXT")
            await conn.commit()
        except Exception:
            pass  # Поле уже существует
        
        # Добавляем поле для закрепленной суммы в рублях
        try:
            await conn.execute("ALTER TABLE deals ADD COLUMN locked_rub_amount TEXT")
            await conn.commit()
        except Exception:
            pass  # Поле уже существует
        
        # Добавляем поле для хранения message_id сообщения "Оплата отмечена"
        try:
            await conn.execute("ALTER TABLE deals ADD COLUMN buyer_paid_message_id INTEGER")
            await conn.commit()
        except Exception:
            pass  # Поле уже существует
        
        # Добавляем поля для хранения message_id уведомлений о начале сделки
        try:
            await conn.execute("ALTER TABLE deals ADD COLUMN seller_deal_started_message_id INTEGER")
            await conn.commit()
        except Exception:
            pass  # Поле уже существует

        try:
            await conn.execute("ALTER TABLE deals ADD COLUMN buyer_deal_started_message_id INTEGER")
            await conn.commit()
        except Exception:
            pass  # Поле уже существует
        
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
            await conn.commit()
        except Exception:
            pass

        from withdraw_queue import ensure_payouts_table
        await ensure_payouts_table()

    print("✅ База данных инициализирована")





# ==================== ФУНКЦИИ РАБОТЫ С ПОЛЬЗОВАТЕЛЯМИ ====================



async def upsert_user(tg_id: int, username: str | None, display_name: str | None = None):
    """Создать или обновить пользователя"""
    # Обрезаем имя до 100 символов если оно слишком длинное
    if username and len(username) > 100:
        username = username[:100]
    
    if display_name and len(display_name) > 200:
        display_name = display_name[:200]
    
    async with db() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id, username, display_name) VALUES (?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, display_name=excluded.display_name",
            (tg_id, username, display_name)
        )
        await conn.commit()


async def update_user_name(tg_id: int, name: str):
    """Обновить имя пользователя"""
    if len(name) > 200:
        name = name[:200]
    
    async with db() as conn:
        await conn.execute(
            "UPDATE users SET name=? WHERE tg_id=?",
            (name, tg_id)
        )
        await conn.commit()


async def update_user_btc_wallet(tg_id: int, btc_wallet: str):
    """Обновить BTC кошелек пользователя"""
    async with db() as conn:
        await conn.execute(
            "UPDATE users SET btc_wallet=? WHERE tg_id=?",
            (btc_wallet, tg_id)
        )
        await conn.commit()


async def is_user_registered(tg_id: int) -> bool:
    """Проверить, зарегистрирован ли пользователь (есть ли имя и BTC кошелек)"""
    user = await get_user(tg_id)
    if not user:
        return False
    return bool(user.get("name") and user.get("btc_wallet"))





async def get_user(tg_id: int) -> dict | None:

    """Получить пользователя"""

    async with db() as conn:

        cur = await conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))

        row = await cur.fetchone()

        # aiosqlite.Row уже поддерживает доступ по ключам
        return dict(row) if row else None



async def find_user_by_username(username: str) -> dict | None:

    """Найти пользователя по username"""

    async with db() as conn:

        cur = await conn.execute("SELECT * FROM users WHERE username=?", (username,))

        row = await cur.fetchone()

        # aiosqlite.Row уже поддерживает доступ по ключам
        return dict(row) if row else None


async def get_all_users(limit: int = 100, offset: int = 0) -> list[dict]:
    """Получить всех пользователей с пагинацией"""
    async with db() as conn:
        cur = await conn.execute(
            "SELECT * FROM users ORDER BY tg_id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        rows = await cur.fetchall()
        # aiosqlite.Row уже поддерживает доступ по ключам
        return [dict(r) for r in rows]


async def get_all_users_count() -> int:
    """Получить общее количество пользователей"""
    async with db() as conn:
        cur = await conn.execute("SELECT COUNT(*) as count FROM users")
        row = await cur.fetchone()
        return row["count"] if row else 0





async def inc_success(tg_id: int):

    """Увеличить счётчик успешных сделок"""

    async with db() as conn:

        await conn.execute(

            "UPDATE users SET success_count = success_count + 1 WHERE tg_id=?", 

            (tg_id,)

        )

        await conn.commit()





def is_admin(user_id: int) -> bool:

    """Проверка является ли пользователь админом"""

    return user_id in ADMIN_IDS

# ==================== ФУНКЦИИ РАБОТЫ СО СДЕЛКАМИ ====================



async def create_deal(

    owner_id: int, min_btc: Decimal, max_btc: Decimal,

    escrow_address: str, *, side: str = "SELL",

    status: str | None = None, buyer_wallet: str | None = None,

    partner_id: int | None = None,

    seller_commission: str | None = None,

    buyer_extra: str | None = None,

    seller_net_loss: str | None = None,

    net_to_seller: str | None = None,

    total_fiat_equivalent: str | None = None

) -> int:

    """Создать сделку"""

    init_status = status or DealStatus.AWAITING_DEPOSIT

    expires_at = (datetime.utcnow() + timedelta(hours=TTL_HOURS)).isoformat()

    

    async with db() as conn:

        cur = await conn.execute(

            "INSERT INTO deals (owner_id, side, min_btc, max_btc, status, escrow_address, "

            "payment_details, buyer_wallet, partner_id, buyer_id, buy_amount, "

            "platform_commission, buyer_extra, seller_net_loss, net_to_seller, "

            "total_fiat_equivalent, created_at, expires_at) "

            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",

            (owner_id, side.upper(), str(min_btc), str(max_btc), init_status, escrow_address,

             None, buyer_wallet, partner_id, None, None, seller_commission, buyer_extra,

             seller_net_loss, net_to_seller, total_fiat_equivalent, 

             datetime.utcnow().isoformat(), expires_at)

        )

        await conn.commit()

        return cur.lastrowid





async def get_deal(deal_id: int) -> dict | None:

    """Получить сделку по ID"""

    async with db() as conn:

        cur = await conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,))

        row = await cur.fetchone()

        if row:
            # aiosqlite.Row уже поддерживает доступ по ключам, просто используем dict()
            return dict(row)
        return None



async def find_deal_by_username_and_status(username: str, status: str) -> dict | None:

    """Найти сделку по username владельца и статусу"""

    async with db() as conn:

        # Сначала находим пользователя по username

        cur = await conn.execute("SELECT tg_id FROM users WHERE username=?", (username,))

        user_row = await cur.fetchone()

        if not user_row:

            return None

        user_id = user_row[0]

        # Теперь находим сделку для этого пользователя с нужным статусом

        cur = await conn.execute(

            "SELECT * FROM deals WHERE owner_id=? AND status=? ORDER BY created_at DESC LIMIT 1",

            (user_id, status)

        )

        row = await cur.fetchone()

        return dict(row) if row else None



async def confirm_deposit(deal_id: int):

    """Подтвердить депозит - обновить статус сделки на ESCROWED или ACTIVE (если реквизиты указаны)"""

    import logging
    logger = logging.getLogger(__name__)
    
    # Проверяем, указаны ли реквизиты
    deal = await get_deal(deal_id)
    if not deal:
        logger.error(f"❌ Сделка {deal_id} не найдена при подтверждении депозита")
        return
    
    payment_details = deal.get("payment_details")
    # Проверяем, что payment_details не только существует, но и не пустой
    # Обрабатываем случаи: None, пустая строка, строка с пробелами
    if payment_details is None:
        has_payment_details = False
    elif isinstance(payment_details, str):
        has_payment_details = bool(payment_details.strip())
    else:
        has_payment_details = bool(payment_details)
    
    logger.info(f"🔍 Подтверждение депозита для сделки {deal_id}: payment_details={payment_details}, has_payment_details={has_payment_details}, текущий статус={deal.get('status')}, тип payment_details={type(payment_details)}")

    if deal.get("status") not in (DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING):
        logger.warning(
            f"⚠️ Подтверждение депозита пропущено для сделки {deal_id}: "
            f"статус {deal.get('status')} не AWAITING_DEPOSIT/CONFIRMING"
        )
        return

    if (deal.get("side") or "").upper() == "GARANT":
        await update_deal_status(deal_id, DealStatus.MATCHED)
        async with db() as conn:
            await conn.execute(
                "UPDATE deals SET matched_at=? WHERE id=? AND (matched_at IS NULL OR matched_at='')",
                (datetime.utcnow().isoformat(), deal_id),
            )
            await conn.commit()
        logger.info(f"✅ Автогарант #{deal_id} открыт после депозита")
        try:
            from handlers.garant_handlers import notify_garant_funded
            await notify_garant_funded(deal_id)
        except Exception as e:
            logger.error(f"❌ Не удалось уведомить участников автогаранта #{deal_id}: {e}", exc_info=True)
        return

    if has_payment_details:
        # Если реквизиты указаны, делаем сделку активной
        await update_deal_status(deal_id, DealStatus.ACTIVE)
        logger.info(f"✅ Сделка {deal_id} активирована (депозит подтвержден, реквизиты указаны: {payment_details[:50] if payment_details else 'N/A'}...)")
        
        # Проверяем, что статус действительно обновился
        updated_deal = await get_deal(deal_id)
        if updated_deal:
            logger.info(f"✅ Проверка: статус сделки {deal_id} после обновления: {updated_deal.get('status')}")
            if updated_deal.get('status') != DealStatus.ACTIVE:
                logger.error(f"❌ ОШИБКА: Статус сделки {deal_id} не обновился на ACTIVE! Текущий статус: {updated_deal.get('status')}")
    else:
        # Если реквизиты не указаны, статус ESCROWED (ожидание реквизитов)
        await update_deal_status(deal_id, DealStatus.ESCROWED)
        logger.info(f"⏳ Сделка {deal_id} переведена в статус ESCROWED (депозит подтвержден, реквизиты не указаны)")


async def claim_deposit_message(chat_id: int, message_id: int) -> bool:
    """Первое сообщение о депозите — True, повтор того же message_id — False."""
    async with db() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS processed_deposit_msgs ("
            "chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, "
            "created_at TEXT NOT NULL, PRIMARY KEY (chat_id, message_id))"
        )
        cur = await conn.execute(
            "INSERT OR IGNORE INTO processed_deposit_msgs (chat_id, message_id, created_at) "
            "VALUES (?,?,?)",
            (int(chat_id), int(message_id), datetime.utcnow().isoformat()),
        )
        await conn.commit()
        return cur.rowcount == 1



async def confirm_latest_awaiting_deposit_for_user(user_id: int) -> int | None:

    """Подтвердить последнюю сделку со статусом AWAITING_DEPOSIT или CONFIRMING для пользователя"""

    async with db() as conn:

        # Ищем последнюю сделку со статусом AWAITING_DEPOSIT или CONFIRMING

        cur = await conn.execute(

            "SELECT id FROM deals WHERE owner_id=? AND status IN (?, ?) ORDER BY created_at DESC LIMIT 1",

            (user_id, DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING)

        )

        row = await cur.fetchone()

        if not row:

            return None

        deal_id = row[0]

        # Обновляем статус на ESCROWED

        await update_deal_status(deal_id, DealStatus.ESCROWED)

        return deal_id



async def send_btc_transaction(escrow_address: str, buyer_wallet: str, amount_btc: Decimal, deal_id: int) -> dict:
    """Отправить BTC транзакцию (заглушка для интеграции с реальным API кошелька)
    
    Args:
        escrow_address: Адрес эскроу кошелька (откуда отправляем)
        buyer_wallet: Адрес кошелька покупателя (куда отправляем)
        amount_btc: Сумма BTC для отправки
        deal_id: ID сделки (для логирования)
    
    Returns:
        dict с результатом: {"success": bool, "tx_id": str | None, "error": str | None}
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"🚀 Автоматический вывод BTC для сделки {deal_id}: {amount_btc} BTC с {escrow_address} на {buyer_wallet}")
    
    # TODO: Здесь должна быть интеграция с реальным API кошелька (Blockchain.info, Bitcoind, и т.д.)
    # Пример интеграции:
    # try:
    #     # Создаем транзакцию через API кошелька
    #     tx_result = await btc_wallet_api.send_transaction(
    #         from_address=escrow_address,
    #         to_address=buyer_wallet,
    #         amount=amount_btc,
    #         private_key=ESCROW_PRIVATE_KEY  # Из конфига или безопасного хранилища
    #     )
    #     return {"success": True, "tx_id": tx_result["txid"], "error": None}
    # except Exception as e:
    #     logger.error(f"Ошибка отправки BTC транзакции: {e}")
    #     return {"success": False, "tx_id": None, "error": str(e)}
    
    # Пока возвращаем успешный результат с заглушкой
    # В реальной системе здесь должна быть проверка баланса и отправка транзакции
    logger.warning(f"⚠️ ВНИМАНИЕ: Функция send_btc_transaction использует заглушку! Реальная транзакция не отправлена.")
    logger.info(f"📝 Заглушка: Транзакция {amount_btc} BTC с {escrow_address} на {buyer_wallet} должна быть выполнена вручную или через API")
    
    # В тестовом режиме возвращаем успех, но в продакшене это должно быть реальной транзакцией
    return {
        "success": True,  # В реальной системе должно быть False, если транзакция не прошла
        "tx_id": f"STUB_TX_{deal_id}_{datetime.utcnow().timestamp()}",
        "error": None
    }


async def auto_release_btc(deal_id: int, bot=None) -> dict:
    """Автоматически выполнить релиз BTC после подтверждения оплаты продавцом
    
    Args:
        deal_id: ID сделки
        bot: Экземпляр бота для отправки уведомлений (опционально)
    
    Returns:
        dict с результатом: {"success": bool, "message": str, "tx_id": str | None}
    """
    import logging
    import asyncio
    logger = logging.getLogger(__name__)
    
    # ЗАЩИТА ОТ ПОВТОРНОГО ВЫЗОВА: используем локальный словарь для отслеживания активных вызовов
    if not hasattr(auto_release_btc, '_active_calls'):
        auto_release_btc._active_calls = {}
    
    # Проверяем, не выполняется ли уже обработка этой сделки
    if deal_id in auto_release_btc._active_calls:
        logger.warning(f"⚠️ [РЕЛИЗ] Функция auto_release_btc уже выполняется для сделки {deal_id}, пропускаем повторный вызов")
        return {"success": True, "message": "Обработка уже выполняется", "tx_id": None}
    
    # Помечаем сделку как обрабатываемую
    auto_release_btc._active_calls[deal_id] = asyncio.get_event_loop().time()
    
    try:
        from decimal import Decimal
        # Получаем данные сделки
        deal = await get_deal(deal_id)
        if not deal:
            error_msg = f"Сделка {deal_id} не найдена"
            logger.error(f"❌ {error_msg}")
            return {"success": False, "message": error_msg, "tx_id": None}
        
        # Проверяем статус сделки
        if deal["status"] != DealStatus.PAYMENT_PENDING:
            error_msg = f"Некорректный статус сделки для автовывода: {deal['status']} (ожидается PaymentPending)"
            logger.warning(f"⚠️ {error_msg}")
            return {"success": False, "message": error_msg, "tx_id": None}
        
        # Проверяем наличие кошелька покупателя
        # Получаем кошелек покупателя (сначала из сделки, потом из профиля)
        buyer_wallet = await get_buyer_wallet(deal)
        if not buyer_wallet or not buyer_wallet.strip():
            error_msg = f"Кошелек покупателя не указан для сделки {deal_id}"
            logger.error(f"❌ {error_msg}")
            return {"success": False, "message": error_msg, "tx_id": None}
        
        # Если кошелек был получен из профиля, сохраняем его в сделке
        if not deal.get("buyer_wallet"):
            await set_deal_buyer_wallet(deal_id, buyer_wallet)
        
        # Получаем адрес эскроу
        escrow_address = deal.get("escrow_address")
        if not escrow_address:
            error_msg = f"Адрес эскроу не указан для сделки {deal_id}"
            logger.error(f"❌ {error_msg}")
            return {"success": False, "message": error_msg, "tx_id": None}
        
        # НОВАЯ ЛОГИКА КОМИССИИ:
        # 1. Покупатель выбирает сумму покупки: buy_amount (в пределах min_btc - max_btc)
        # 2. Покупатель платит продавцу: buy_amount (полная сумма)
        # 3. Платформа удерживает 0% с суммы покупки (временно)
        # 4. Покупатель получает: buy_amount - (buy_amount * 0%) = buy_amount * 1.0 (100% от суммы покупки)
        # 5. Продавец получает: buy_amount (полная сумма, которую платит покупатель)
        
        # Определяем сумму покупки (buy_amount) - это база для расчета комиссии
        # Если buy_amount не указан, используем max_btc (для обратной совместимости)
        purchase_amount_str = deal.get("buy_amount") or deal.get("max_btc")
        if not purchase_amount_str:
            error_msg = f"Сумма покупки (buy_amount или max_btc) не указана для сделки {deal_id}"
            logger.error(f"❌ {error_msg}")
            return {"success": False, "message": error_msg, "tx_id": None}
        
        try:
            purchase_amount = Decimal(str(purchase_amount_str))
        except Exception as e:
            error_msg = f"Некорректная сумма покупки: {purchase_amount_str}"
            logger.error(f"❌ {error_msg}: {e}")
            return {"success": False, "message": error_msg, "tx_id": None}
        
        # Рассчитываем сумму для вывода: покупатель получает выбранную сумму минус комиссия платформы (0% временно)
        # Покупатель выбрал purchase_amount, платформа забирает 0% комиссии (временно)
        # Покупатель получает оставшуюся сумму (purchase_amount - комиссия) = 100% от суммы покупки
        from config import BUYER_COMMISSION_RATE
        q = Decimal("0.00000001")
        commission = (purchase_amount * BUYER_COMMISSION_RATE).quantize(q, rounding=ROUND_DOWN)
        # Покупатель получает сумму покупки минус комиссия платформы (100% от суммы покупки при 0% комиссии)
        withdraw_amount = (purchase_amount - commission).quantize(q, rounding=ROUND_DOWN)
        
        logger.info(f"💰 [РЕЛИЗ] Начинаем автовывод BTC для сделки {deal_id}: {withdraw_amount} BTC (сумма покупки: {purchase_amount} BTC, комиссия платформы: {commission} BTC) на {buyer_wallet}")
        
        # Используем ту же систему, что и для возврата депозита - через forwarder.py и withdraw_queue.json
        buyer_id = deal["partner_id"] if (deal.get("side") or "").upper() in ("SELL", "GARANT") else deal["owner_id"]
        
        logger.info(f"💰 [РЕЛИЗ] === СОЗДАНИЕ ЗАДАЧИ НА ВЫВОД BTC ПОКУПАТЕЛЮ ===")
        logger.info(f"💰 [РЕЛИЗ] Сделка #{deal_id}, покупатель {buyer_id}")
        logger.info(f"💰 [РЕЛИЗ] Кошелек: {buyer_wallet}, сумма к выводу: {withdraw_amount} BTC")
        
        try:
            amount_str = f"{withdraw_amount:.8f}".rstrip('0').rstrip('.')
            logger.info(f"💰 [РЕЛИЗ] Задача: сделка #{deal_id}, {amount_str} BTC → {buyer_wallet}")
            from withdraw_queue import enqueue_payout
            queued = await enqueue_payout(
                deal_id=deal_id,
                user_id=buyer_id,
                wallet=buyer_wallet,
                amount=amount_str,
                payout_type="release",
            )
            if queued.get("created"):
                logger.info(f"✅ [РЕЛИЗ] Задача вывода BTC добавлена в очередь, forwarder.py обработает её автоматически")
                tx_result = {"success": True, "tx_id": None, "message": "Задача добавлена в очередь, вывод будет выполнен автоматически"}
            else:
                logger.info(f"💰 [РЕЛИЗ] Задача не добавлена (дубликат)")
                tx_result = {"success": True, "tx_id": None, "message": "Задача уже существует в очереди"}
            
        except Exception as e:
            logger.error(f"❌ [РЕЛИЗ] Ошибка при добавлении задачи вывода: {e}", exc_info=True)
            import traceback
            logger.error(f"❌ [РЕЛИЗ] Traceback: {traceback.format_exc()}")
            return {"success": False, "message": f"Ошибка при создании задачи вывода: {e}", "tx_id": None}
        
        # Транзакция будет выполнена forwarder.py
        # ВАЖНО: НЕ обновляем статус на RELEASED здесь - это будет сделано в forwarder.py после получения подтверждения вывода
        logger.info(f"✅ [РЕЛИЗ] Задача вывода BTC создана для сделки {deal_id}, forwarder.py обработает её автоматически")
        logger.info(f"⏳ [РЕЛИЗ] Статус сделки останется PAYMENT_PENDING до получения подтверждения вывода от @BmbP2p_bot")
        
        # Сохраняем seller_confirmed_at, но НЕ меняем статус на RELEASED
        # Статус будет обновлен в forwarder.py после получения подтверждения "✅ Ваша заявка на вывод одобрена"
        async with db() as conn:
            # Проверяем текущий статус и completed_at
            cur = await conn.execute(
                "SELECT status, completed_at, seller_confirmed_at FROM deals WHERE id = ?",
                (deal_id,)
            )
            current_row = await cur.fetchone()
            if current_row:
                current_status = current_row[0]
                current_completed_at = current_row[1]
                current_seller_confirmed_at = current_row[2]
                
                # Если сделка уже завершена (completed_at установлен), пропускаем
                if current_completed_at:
                    logger.warning(f"⚠️ [РЕЛИЗ] Сделка {deal_id} уже завершена (completed_at: {current_completed_at}), пропускаем повторную обработку")
                    return {"success": True, "message": "Сделка уже завершена", "tx_id": None}
                
                # Если статус уже RELEASED, значит подтверждение уже получено ранее
                if current_status == DealStatus.RELEASED:
                    logger.warning(f"⚠️ [РЕЛИЗ] Сделка {deal_id} уже имеет статус RELEASED, пропускаем")
                    return {"success": True, "message": "Сделка уже завершена", "tx_id": None}
                
                # Сохраняем seller_confirmed_at, но НЕ меняем статус (остается PAYMENT_PENDING)
                if not current_seller_confirmed_at:
                    await conn.execute(
                        "UPDATE deals SET seller_confirmed_at=? WHERE id=?",
                        (datetime.utcnow().isoformat(), deal_id)
                    )
                    await conn.commit()
                    logger.info(f"✅ [РЕЛИЗ] seller_confirmed_at сохранен для сделки {deal_id}, статус останется PAYMENT_PENDING до подтверждения вывода")
        
        # ВАЖНО: НЕ отправляем уведомления и НЕ обновляем статистику здесь
        # Это будет сделано в функции complete_deal_after_withdrawal_confirmation после получения подтверждения вывода
        logger.info(f"⏳ [РЕЛИЗ] Ожидаем подтверждения вывода от @BmbP2p_bot для сделки {deal_id}")
        
        success_msg = f"Задача вывода BTC создана, ожидаем подтверждения. Сделка останется в статусе PAYMENT_PENDING до подтверждения вывода."
        logger.info(f"✅ Задача вывода BTC создана для сделки {deal_id}, forwarder.py обработает её и завершит сделку после подтверждения")
        
        return {"success": True, "message": success_msg, "tx_id": None, "waiting_confirmation": True}
        
    except Exception as e:
        error_msg = f"Неожиданная ошибка при автовыводе BTC: {e}"
        logger.error(f"❌ {error_msg}", exc_info=True)
        
        # Уведомляем админов об ошибке
        if bot:
            from config import ADMIN_IDS, ADMIN_CHAT_ID
            admin_notification = (
                f"❌ <b>Критическая ошибка автовывода BTC</b>\n\n"
                f"Сделка #{deal_id}\n"
                f"Ошибка: {str(e)}\n\n"
                f"Требуется ручное вмешательство."
            )
            targets = list(ADMIN_IDS)
            if ADMIN_CHAT_ID:
                targets.append(ADMIN_CHAT_ID)
            for tid in targets:
                try:
                    await bot.send_message(tid, admin_notification, parse_mode="HTML")
                except Exception:
                    pass
        
        return {"success": False, "message": error_msg, "tx_id": None}
    
    finally:
        # Удаляем сделку из активных вызовов (всегда выполняется)
        if deal_id in auto_release_btc._active_calls:
            del auto_release_btc._active_calls[deal_id]
            logger.info(f"🔓 [РЕЛИЗ] Сделка {deal_id} удалена из активных вызовов")


async def update_deal_status(deal_id: int, status: str):

    """Обновить статус сделки"""

    import logging
    logger = logging.getLogger(__name__)

    async with db() as conn:

        # Проверяем текущий статус перед обновлением
        cur = await conn.execute("SELECT status FROM deals WHERE id=?", (deal_id,))
        row = await cur.fetchone()
        old_status = row[0] if row else None
        logger.info(f"🔄 Обновление статуса сделки {deal_id}: {old_status} -> {status}")

        # Если статус меняется на ACTIVE, обновляем expires_at на текущее время + TTL в той же транзакции
        if status == DealStatus.ACTIVE:
            from config import TTL_HOURS
            from datetime import timedelta
            new_expires_at = (datetime.utcnow() + timedelta(hours=TTL_HOURS)).isoformat()
            await conn.execute("UPDATE deals SET status=?, expires_at=? WHERE id=?", (status, new_expires_at, deal_id))
            logger.info(f"🕒 Обновлены status и expires_at для сделки {deal_id}: status='{status}', expires_at='{new_expires_at}'")
        else:
            await conn.execute("UPDATE deals SET status=? WHERE id=?", (status, deal_id))

        await conn.commit()

        # Публикуем заявку в публичный канал при переходе в ACTIVE
        if status == DealStatus.ACTIVE and old_status != DealStatus.ACTIVE:
            try:
                await send_public_request_notification(deal_id)
            except Exception:
                pass

        # Проверяем, что статус и expires_at действительно обновились (в новом соединении для проверки)
        # Используем новое соединение, чтобы убедиться, что изменения видны
        async with db() as check_conn:
            cur = await check_conn.execute("SELECT status, expires_at, side, owner_id FROM deals WHERE id=?", (deal_id,))
            row = await cur.fetchone()
            if row:
                actual_status = row[0]
                actual_expires_at = row[1]
                actual_side = row[2]
                actual_owner_id = row[3]
                logger.info(f"✅ Проверка сделки {deal_id} после обновления (новое соединение): status='{actual_status}' (длина={len(actual_status)}), side='{actual_side}', expires_at='{actual_expires_at}', owner_id={actual_owner_id}")
                
                # Проверяем, что статус точно равен ожидаемому
                if actual_status != status:
                    logger.error(f"❌ ОШИБКА: Статус сделки {deal_id} не совпадает! Ожидалось: '{status}' (длина={len(status)}, bytes={status.encode()}), получено: '{actual_status}' (длина={len(actual_status)}, bytes={actual_status.encode()})")
                
                if status == DealStatus.ACTIVE:
                    # Проверяем, что expires_at в будущем
                    try:
                        expires_dt = datetime.fromisoformat(actual_expires_at.replace('Z', '+00:00'))
                        now_dt = datetime.utcnow()
                        logger.info(f"🕒 Сравнение времени для сделки {deal_id}: expires_at={expires_dt}, now={now_dt}, разница={(expires_dt - now_dt).total_seconds()} секунд")
                        if expires_dt <= now_dt:
                            logger.error(f"❌ ОШИБКА: expires_at сделки {deal_id} в прошлом! expires_at={expires_dt}, now={now_dt}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка парсинга expires_at: {e}", exc_info=True)
                    
                    # Проверяем, видна ли сделка в запросе активных сделок
                    # Нормализуем side в верхний регистр для сравнения
                    actual_side_upper = actual_side.upper()
                    test_cur = await check_conn.execute(
                        "SELECT id FROM deals WHERE status=? AND UPPER(side)=? AND expires_at>? AND owner_id<>?",
                        (DealStatus.ACTIVE, actual_side_upper, datetime.utcnow().isoformat(), actual_owner_id)
                    )
                    test_rows = await test_cur.fetchall()
                    logger.info(f"🔍 Тестовый запрос активных сделок (side={actual_side}, exclude_owner_id={actual_owner_id}): найдено {len(test_rows)} сделок, сделка {deal_id} {'найдена' if any(r[0] == deal_id for r in test_rows) else 'НЕ НАЙДЕНА'}")
            else:
                logger.error(f"❌ ОШИБКА: Сделка {deal_id} не найдена после обновления!")





async def set_deal_payment_details(deal_id: int, details: str):

    """Установить реквизиты продавца"""

    import logging
    logger = logging.getLogger(__name__)

    async with db() as conn:

        await conn.execute("UPDATE deals SET payment_details=? WHERE id=?", (details, deal_id))

        await conn.commit()
        
        # Проверяем, что реквизиты действительно сохранены
        cur = await conn.execute("SELECT payment_details FROM deals WHERE id=?", (deal_id,))
        row = await cur.fetchone()
        if row:
            saved_details = row[0]
            logger.info(f"💾 Реквизиты сохранены для сделки {deal_id}: payment_details={saved_details[:50] if saved_details else 'None'}...")
        else:
            logger.error(f"❌ Сделка {deal_id} не найдена после сохранения реквизитов")





async def complete_deal_after_withdrawal_confirmation(deal_id: int, bot=None):
    """Завершить сделку после подтверждения вывода BTC от @BmbP2p_bot
    
    Args:
        deal_id: ID сделки
        bot: Экземпляр бота для отправки уведомлений (опционально)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"🔄 [ЗАВЕРШЕНИЕ] Начинаю завершение сделки {deal_id}")
        deal = await get_deal(deal_id)
        if not deal:
            logger.error(f"❌ [ЗАВЕРШЕНИЕ] Сделка {deal_id} не найдена")
            return False
        
        logger.info(f"📋 [ЗАВЕРШЕНИЕ] Текущий статус сделки {deal_id}: {deal['status']}")
        
        # Обновляем статус на RELEASED
        async with db() as conn:
            # Сначала проверяем, не завершена ли уже сделка
            cur = await conn.execute(
                "SELECT status, completed_at FROM deals WHERE id = ?",
                (deal_id,)
            )
            row = await cur.fetchone()
            if not row:
                logger.error(f"❌ [ЗАВЕРШЕНИЕ] Сделка {deal_id} не найдена в базе данных")
                return False
            
            current_status = row[0]
            completed_at = row[1]
            
            # Если сделка уже завершена (completed_at установлен), возвращаем True
            if completed_at:
                logger.info(f"✅ [ЗАВЕРШЕНИЕ] Сделка {deal_id} уже завершена (completed_at: {completed_at}), возвращаю True")
                return True
            
            # Если статус уже RELEASED, но completed_at не установлен, устанавливаем его
            if current_status == DealStatus.RELEASED:
                logger.info(f"✅ [ЗАВЕРШЕНИЕ] Сделка {deal_id} уже в статусе RELEASED, устанавливаю completed_at")
                cur = await conn.execute(
                    "UPDATE deals SET completed_at=? WHERE id=? AND completed_at IS NULL",
                    (datetime.utcnow().isoformat(), deal_id)
                )
                await conn.commit()
                logger.info(f"✅ [ЗАВЕРШЕНИЕ] completed_at установлен для сделки {deal_id}")
            else:
                # Обновляем статус на RELEASED (пробуем с условием PAYMENT_PENDING, но если не получится, пробуем без условия)
                cur = await conn.execute(
                    "UPDATE deals SET status=?, completed_at=? WHERE id=? AND status = ? AND completed_at IS NULL",
                    (DealStatus.RELEASED, datetime.utcnow().isoformat(), deal_id, DealStatus.PAYMENT_PENDING)
                )
                rows_affected = cur.rowcount
                
                if rows_affected == 0:
                    # Если не получилось обновить с условием PAYMENT_PENDING, пробуем обновить без условия (на случай, если статус уже изменился)
                    logger.warning(f"⚠️ [ЗАВЕРШЕНИЕ] Не удалось обновить статус с условием PAYMENT_PENDING, пробую обновить без условия")
                    cur = await conn.execute(
                        "UPDATE deals SET status=?, completed_at=? WHERE id=? AND completed_at IS NULL",
                        (DealStatus.RELEASED, datetime.utcnow().isoformat(), deal_id)
                    )
                    rows_affected = cur.rowcount
                
                await conn.commit()
                
                if rows_affected > 0:
                    logger.info(f"✅ [ЗАВЕРШЕНИЕ] Статус сделки {deal_id} обновлен на RELEASED (rows_affected: {rows_affected})")
                else:
                    # Проверяем текущий статус еще раз
                    cur = await conn.execute("SELECT status, completed_at FROM deals WHERE id = ?", (deal_id,))
                    row = await cur.fetchone()
                    if row:
                        logger.warning(f"⚠️ [ЗАВЕРШЕНИЕ] Не удалось обновить статус сделки {deal_id} (rows_affected: {rows_affected}). Текущий статус: {row[0]}, completed_at: {row[1]}")
                        # Если статус уже RELEASED, возвращаем True
                        if row[0] == DealStatus.RELEASED:
                            logger.info(f"✅ [ЗАВЕРШЕНИЕ] Сделка {deal_id} уже в статусе RELEASED, возвращаю True")
                            return True
                    return False
        
        # Обновляем статистику успешных сделок
        await inc_success(deal["owner_id"])
        if deal.get("partner_id"):
            await inc_success(deal["partner_id"])
        
        # Обновляем общую статистику
        await update_user_stats_on_deal_complete(deal_id)
        await update_daily_stats()
        
        # Определяем покупателя и продавца
        buyer_id = deal["partner_id"] if (deal.get("side") or "").upper() in ("SELL", "GARANT") else deal["owner_id"]
        seller_id = deal["owner_id"] if (deal.get("side") or "").upper() in ("SELL", "GARANT") else deal["partner_id"]
        buyer_wallet = deal.get("buyer_wallet") or ""
        
        # Получаем сумму вывода
        from decimal import Decimal
        purchase_amount_str = deal.get("buy_amount") or deal.get("max_btc")
        if purchase_amount_str:
            try:
                purchase_amount = Decimal(str(purchase_amount_str))
                from config import BUYER_COMMISSION_RATE
                q = Decimal("0.00000001")
                commission = (purchase_amount * BUYER_COMMISSION_RATE).quantize(q, rounding=ROUND_DOWN)
                withdraw_amount = (purchase_amount - commission).quantize(q, rounding=ROUND_DOWN)
                amount_str = f"{withdraw_amount:.8f}".rstrip('0').rstrip('.')
            except:
                amount_str = "N/A"
        else:
            amount_str = "N/A"
        
        # Отправляем уведомления через aiogram Bot, если bot не передан
        bot_created = False
        if not bot:
            try:
                from config import BOT_TOKEN
                from utils import create_bot
                bot = create_bot(BOT_TOKEN)
                bot_created = True
                logger.info(f"✅ [ЗАВЕРШЕНИЕ] Создан экземпляр aiogram Bot для отправки уведомлений")
            except Exception as e:
                logger.error(f"❌ [ЗАВЕРШЕНИЕ] Не удалось создать экземпляр aiogram Bot: {e}", exc_info=True)
                bot = None
        
        if bot:
            # Удаляем старое сообщение "✅ Оплата отмечена. Ожидайте подтверждения продавцом."
            try:
                async with db() as conn:
                    cur = await conn.execute(
                        "SELECT buyer_paid_message_id FROM deals WHERE id = ?",
                        (deal_id,)
                    )
                    row = await cur.fetchone()
                    if row and row[0]:
                        paid_message_id = row[0]
                        try:
                            await bot.delete_message(buyer_id, paid_message_id)
                            logger.info(f"✅ [ЗАВЕРШЕНИЕ] Удалено старое сообщение 'Оплата отмечена' (message_id: {paid_message_id}) для покупателя {buyer_id}")
                        except Exception as e:
                            logger.error(f"❌ [ЗАВЕРШЕНИЕ] Не удалось удалить старое сообщение (message_id: {paid_message_id}) для покупателя {buyer_id}: {e}", exc_info=True)
                            # Пробуем отредактировать сообщение вместо удаления
                            try:
                                await bot.edit_message_text(
                                    chat_id=buyer_id,
                                    message_id=paid_message_id,
                                    text="✅ Сделка завершена"
                                )
                                logger.info(f"✅ [ЗАВЕРШЕНИЕ] Сообщение 'Оплата отмечена' отредактировано для покупателя {buyer_id}")
                            except Exception as e2:
                                logger.error(f"❌ [ЗАВЕРШЕНИЕ] Не удалось отредактировать сообщение: {e2}", exc_info=True)
            except Exception as e:
                logger.warning(f"⚠️ [ЗАВЕРШЕНИЕ] Ошибка при попытке удалить старое сообщение: {e}", exc_info=True)
            
            # 🎉 УВЕДОМЛЕНИЕ покупателю
            logger.info(f"📨 [ЗАВЕРШЕНИЕ] Отправляю уведомление покупателю {buyer_id} о завершении сделки {deal_id}")
            buyer_notification = (
                "🎉 <b>Спасибо за сделку!</b>\n\n"
                f"✅ <b>Сделка #{deal_id} успешно завершена</b>\n\n"
                f"💰 BTC автоматически переведены покупателю.\n\n"
                f"💡 <b>Помогите улучшить сервис - оставьте отзыв о контрагенте!</b>"
            )
            
            from keyboards import rate_partner_kb
            try:
                # Отправляем уведомление напрямую через bot.send_message для надежности
                reply_markup = rate_partner_kb(deal_id, seller_id) if seller_id else None
                await bot.send_message(
                    buyer_id,
                    buyer_notification,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                logger.info(f"✅ [ЗАВЕРШЕНИЕ] Уведомление покупателю {buyer_id} отправлено успешно")
            except Exception as e:
                logger.error(f"❌ [ЗАВЕРШЕНИЕ] Ошибка отправки уведомления покупателю {buyer_id}: {e}", exc_info=True)
                # Пробуем через send_notification как запасной вариант
                try:
                    await send_notification(
                        bot,
                        buyer_id,
                        buyer_notification,
                        reply_markup=rate_partner_kb(deal_id, seller_id) if seller_id else None
                    )
                    logger.info(f"✅ [ЗАВЕРШЕНИЕ] Уведомление покупателю {buyer_id} отправлено через send_notification")
                except Exception as e2:
                    logger.error(f"❌ [ЗАВЕРШЕНИЕ] Ошибка отправки уведомления через send_notification: {e2}", exc_info=True)
            
            # 🎉 УВЕДОМЛЕНИЕ продавцу
            if seller_id:
                logger.info(f"📨 [ЗАВЕРШЕНИЕ] Отправляю уведомление продавцу {seller_id} о завершении сделки {deal_id}")
                seller_notification = (
                    "🎉 <b>Спасибо за сделку!</b>\n\n"
                    f"✅ <b>Сделка #{deal_id} успешно завершена</b>\n\n"
                    f"💰 BTC автоматически переведены покупателю.\n\n"
                    f"💡 <b>Помогите улучшить сервис - оставьте отзыв о контрагенте!</b>"
                )
                
                try:
                    # Отправляем уведомление продавцу
                    seller_reply_markup = rate_partner_kb(deal_id, buyer_id) if buyer_id else None
                    await bot.send_message(
                        seller_id,
                        seller_notification,
                        reply_markup=seller_reply_markup,
                        parse_mode="HTML"
                    )
                    logger.info(f"✅ [ЗАВЕРШЕНИЕ] Уведомление продавцу {seller_id} отправлено успешно")
                except Exception as e:
                    logger.error(f"❌ [ЗАВЕРШЕНИЕ] Ошибка отправки уведомления продавцу {seller_id}: {e}", exc_info=True)
                    # Пробуем через send_notification как запасной вариант
                    try:
                        await send_notification(
                            bot,
                            seller_id,
                            seller_notification,
                            reply_markup=rate_partner_kb(deal_id, buyer_id) if buyer_id else None
                        )
                        logger.info(f"✅ [ЗАВЕРШЕНИЕ] Уведомление продавцу {seller_id} отправлено через send_notification")
                    except Exception as e2:
                        logger.error(f"❌ [ЗАВЕРШЕНИЕ] Ошибка отправки уведомления продавцу через send_notification: {e2}", exc_info=True)
            
            # Удаляем уведомления о начале сделки для продавца и покупателя
            try:
                async with db() as conn:
                    cur = await conn.execute(
                        "SELECT seller_deal_started_message_id, buyer_deal_started_message_id FROM deals WHERE id = ?",
                        (deal_id,)
                    )
                    row = await cur.fetchone()
                    if row:
                        # Удаляем уведомление продавца о начале сделки
                        if row[0]:
                            try:
                                await bot.delete_message(seller_id, row[0])
                                logger.info(f"✅ [ЗАВЕРШЕНИЕ] Удалено уведомление о начале сделки для продавца (message_id: {row[0]})")
                            except Exception as e:
                                logger.warning(f"⚠️ [ЗАВЕРШЕНИЕ] Не удалось удалить уведомление о начале сделки для продавца (message_id: {row[0]}): {e}")
                        
                        # Удаляем уведомление покупателя о начале сделки
                        if row[1]:
                            try:
                                await bot.delete_message(buyer_id, row[1])
                                logger.info(f"✅ [ЗАВЕРШЕНИЕ] Удалено уведомление о начале сделки для покупателя (message_id: {row[1]})")
                            except Exception as e:
                                logger.warning(f"⚠️ [ЗАВЕРШЕНИЕ] Не удалось удалить уведомление о начале сделки для покупателя (message_id: {row[1]}): {e}")
            except Exception as e:
                logger.warning(f"⚠️ [ЗАВЕРШЕНИЕ] Ошибка при попытке удалить уведомления о начале сделки: {e}", exc_info=True)
            
            # Закрываем сессию бота, если мы его создали
            if bot_created:
                try:
                    await bot.session.close()
                    logger.info(f"✅ [ЗАВЕРШЕНИЕ] Сессия aiogram Bot закрыта")
                except Exception as e:
                    logger.debug(f"⚠️ [ЗАВЕРШЕНИЕ] Ошибка при закрытии сессии бота (может быть уже закрыта): {e}")
        
        logger.info(f"✅ [ЗАВЕРШЕНИЕ] Сделка {deal_id} успешно завершена после подтверждения вывода")
        return True
        
    except Exception as e:
        logger.error(f"❌ [ЗАВЕРШЕНИЕ] Ошибка при завершении сделки {deal_id}: {e}", exc_info=True)
        return False


async def set_deal_buyer_wallet(deal_id: int, wallet: str):

    """Установить кошелёк покупателя"""

    async with db() as conn:

        await conn.execute("UPDATE deals SET buyer_wallet=? WHERE id=?", (wallet, deal_id))

        await conn.commit()





async def set_partner(deal_id: int, user_id: int):

    """Установить партнёра по сделке"""

    async with db() as conn:

        await conn.execute("UPDATE deals SET partner_id=? WHERE id=?", (user_id, deal_id))

        await conn.commit()





async def set_buy_amount(deal_id: int, amount: Decimal):

    """Установить сумму покупки"""

    async with db() as conn:

        await conn.execute("UPDATE deals SET buy_amount=? WHERE id=?", (str(amount), deal_id))

        await conn.commit()





async def list_active_deals_by_side(side: str, exclude_owner_id: int | None = None, limit: int = 10) -> list[dict]:

    """Получить активные заявки"""

    import logging
    logger = logging.getLogger(__name__)

    now_iso = datetime.utcnow().isoformat()
    logger.info(f"🔍 list_active_deals_by_side: side={side}, exclude_owner_id={exclude_owner_id}, now_iso={now_iso}, limit={limit}")

    async with db() as conn:

        # Сначала проверим все сделки со статусом ACTIVE (для отладки)
        cur = await conn.execute(
            "SELECT id, status, side, expires_at, owner_id, payment_details FROM deals WHERE status=? ORDER BY id DESC LIMIT 10",
            (DealStatus.ACTIVE,)
        )
        all_active_deals = await cur.fetchall()
        logger.info(f"🔍 ВСЕ сделки со статусом ACTIVE в базе: {len(all_active_deals)} шт.")
        for d in all_active_deals:
            logger.info(f"  - Сделка #{d[0]}: status='{d[1]}', side='{d[2]}', expires_at={d[3]}, owner_id={d[4]}, payment_details={'есть' if d[5] else 'нет'}")

        if exclude_owner_id:

            # Проверяем все активные сделки с нужным side и без фильтра по expires_at
            # Нормализуем side в верхний регистр для сравнения
            side_upper = side.upper()
            cur = await conn.execute(
                "SELECT id, status, side, expires_at, owner_id FROM deals WHERE status=? AND UPPER(side)=? AND owner_id<>? ORDER BY id DESC LIMIT 20",
                (DealStatus.ACTIVE, side_upper, exclude_owner_id)
            )
            all_deals = await cur.fetchall()
            logger.info(f"🔍 Активные сделки (status={DealStatus.ACTIVE}, side={side}, exclude_owner_id={exclude_owner_id}): {len(all_deals)} шт.")
            for d in all_deals:
                expires_comparison = d[3] > now_iso if d[3] else 'None'
                logger.info(f"  - Сделка #{d[0]}: status='{d[1]}', side='{d[2]}', expires_at='{d[3]}', owner_id={d[4]}, expires_at>now={expires_comparison} (now_iso='{now_iso}')")

            # Теперь выполняем основной запрос
            # Нормализуем side в верхний регистр для сравнения
            side_upper = side.upper()
            cur = await conn.execute(

                "SELECT * FROM deals WHERE status=? AND UPPER(side)=? AND expires_at>? AND owner_id<>? "

                "ORDER BY id DESC LIMIT ?",

                (DealStatus.ACTIVE, side_upper, now_iso, exclude_owner_id, limit)

            )

        else:

            # Сначала проверяем все активные сделки без фильтра по expires_at
            # Нормализуем side в верхний регистр для сравнения
            side_upper = side.upper()
            cur = await conn.execute(
                "SELECT id, status, side, expires_at FROM deals WHERE status=? AND UPPER(side)=? ORDER BY id DESC LIMIT 20",
                (DealStatus.ACTIVE, side_upper)
            )
            all_deals = await cur.fetchall()
            logger.info(f"🔍 Все активные сделки (без фильтра expires_at): {len(all_deals)} шт.")
            for d in all_deals:
                logger.info(f"  - Сделка #{d[0]}: status={d[1]}, side={d[2]}, expires_at={d[3]}, expires_at>now={d[3] > now_iso if d[3] else 'None'}")

            # Нормализуем side в верхний регистр для сравнения
            side_upper = side.upper()
            cur = await conn.execute(

                "SELECT * FROM deals WHERE status=? AND UPPER(side)=? AND expires_at>? ORDER BY id DESC LIMIT ?",

                (DealStatus.ACTIVE, side_upper, now_iso, limit)

            )

        rows = await cur.fetchall()
        # aiosqlite.Row уже поддерживает доступ по ключам
        deals = [dict(r) for r in rows]
        logger.info(f"📋 list_active_deals_by_side: найдено {len(deals)} сделок после фильтрации")
        if deals:
            for d in deals:
                logger.info(f"  ✅ Сделка #{d['id']}: status={d.get('status')}, side={d.get('side')}, expires_at={d.get('expires_at')}")

        return deals





async def expire_old_deals() -> list[dict]:

    """Отметить истёкшие заявки как отменённые и вернуть список истекших сделок для обработки возврата депозита"""

    now_iso = datetime.utcnow().isoformat()

    expired_deals = []

    async with db() as conn:

        # Получаем истекшие сделки перед обновлением статуса
        cur = await conn.execute(
            "SELECT * FROM deals WHERE expires_at<=? AND status IN (?, ?, ?, ?, ?) "
            "AND NOT (UPPER(IFNULL(side,''))='GARANT' AND status=?)",
            (now_iso, DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING, DealStatus.ESCROWED, 
             DealStatus.ACTIVE, DealStatus.MATCHED, DealStatus.MATCHED)
        )
        expired = await cur.fetchall()
        
        for deal in expired:
            d = dict(deal)
            # Сохраняем информацию о сделках, которые требуют возврата депозита
            # Только для заявок на продажу (SELL) и только если депозит был внесен
            # Не включаем MATCHED, так как там уже есть покупатель и другая логика
            if d["side"] == "SELL" and d["status"] in [DealStatus.CONFIRMING, DealStatus.ESCROWED, DealStatus.ACTIVE]:
                expired_deals.append(d)

        # Обновляем статус всех истекших сделок
        await conn.execute(
            "UPDATE deals SET status=? WHERE expires_at<=? AND status IN (?, ?, ?, ?, ?) "
            "AND NOT (UPPER(IFNULL(side,''))='GARANT' AND status=?)",
            (DealStatus.CANCELLED, now_iso, DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING, 
             DealStatus.ESCROWED, DealStatus.ACTIVE, DealStatus.MATCHED, DealStatus.MATCHED)
        )

        await conn.commit()

    return expired_deals





async def claim_deal_exclusive(deal_id: int, partner_id: int) -> bool:

    """Эксклюзивно захватить сделку"""

    async with db() as conn:

        cur = await conn.execute(

            "UPDATE deals SET status=?, partner_id=? WHERE id=? AND status=?",

            (DealStatus.MATCHED, partner_id, deal_id, DealStatus.ACTIVE)

        )

        await conn.commit()

        return cur.rowcount == 1





async def get_user_active_deals(user_id: int, exclude_garant: bool = False, only_garant: bool = False) -> list[dict]:

    """Получить активные сделки пользователя"""

    active_statuses = [

        DealStatus.AWAITING_DEPOSIT,

        DealStatus.CONFIRMING,

        DealStatus.ESCROWED,

        DealStatus.ACTIVE,

        DealStatus.MATCHED,

        DealStatus.PAYMENT_PENDING

    ]

    async with db() as conn:

        placeholders = ','.join('?' * len(active_statuses))

        query = f"SELECT * FROM deals WHERE (owner_id=? OR partner_id=?) AND status IN ({placeholders})"

        if exclude_garant:
            query += " AND (side != 'GARANT' OR side IS NULL)"
        elif only_garant:
            query += " AND side = 'GARANT'"

        query += " ORDER BY created_at DESC"

        cur = await conn.execute(
            query,
            (user_id, user_id, *active_statuses)
        )

        rows = await cur.fetchall()

        return [dict(r) for r in rows]





async def get_user_history_deals(user_id: int, limit: int = None, offset: int = 0, exclude_garant: bool = False, only_garant: bool = False) -> list[dict]:

    """Получить историю сделок с пагинацией

    Args:
        user_id: ID пользователя
        limit: Количество сделок на странице (по умолчанию все)
        offset: Смещение для пагинации
    """

    history_statuses = [DealStatus.RELEASED, DealStatus.CANCELLED, DealStatus.DISPUTED]

    async with db() as conn:

        placeholders = ','.join('?' * len(history_statuses))
        
        query = f"SELECT * FROM deals WHERE (owner_id=? OR partner_id=?) AND status IN ({placeholders})"

        if exclude_garant:
            query += " AND (side != 'GARANT' OR side IS NULL)"
        elif only_garant:
            query += " AND side = 'GARANT'"

        query += " ORDER BY created_at DESC"
        
        if limit:
            query += f" LIMIT ? OFFSET ?"
            params = (user_id, user_id, *history_statuses, limit, offset)
        else:
            params = (user_id, user_id, *history_statuses)

        cur = await conn.execute(query, params)

        rows = await cur.fetchall()

        return [dict(r) for r in rows]


async def get_user_history_deals_count(user_id: int, exclude_garant: bool = False, only_garant: bool = False) -> int:
    """Получить общее количество завершенных сделок пользователя"""
    history_statuses = [DealStatus.RELEASED, DealStatus.CANCELLED, DealStatus.DISPUTED]
    
    async with db() as conn:
        placeholders = ','.join('?' * len(history_statuses))

        query = f"SELECT COUNT(*) as count FROM deals WHERE (owner_id=? OR partner_id=?) AND status IN ({placeholders})"

        if exclude_garant:
            query += " AND (side != 'GARANT' OR side IS NULL)"
        elif only_garant:
            query += " AND side = 'GARANT'"

        cur = await conn.execute(
            query,
            (user_id, user_id, *history_statuses)
        )
        row = await cur.fetchone()
        return row["count"] if row else 0





# ==================== РЕЙТИНГИ ====================



async def add_rating(deal_id: int, from_user_id: int, to_user_id: int, stars: int, 

                    speed_rating: int | None = None, comment: str | None = None):

    """Добавить рейтинг"""

    if not (1 <= stars <= 5):

        raise ValueError("Рейтинг должен быть от 1 до 5")

    

    async with db() as conn:

        await conn.execute(

            "INSERT INTO ratings (deal_id, from_user_id, to_user_id, stars, speed_rating, comment, created_at) "

            "VALUES (?, ?, ?, ?, ?, ?, ?)",

            (deal_id, from_user_id, to_user_id, stars, speed_rating, comment, datetime.utcnow().isoformat())

        )

        await conn.commit()

    

    await update_user_rating(to_user_id)





async def update_user_rating(user_id: int):

    """Пересчитать средний рейтинг пользователя"""

    async with db() as conn:

        cur = await conn.execute(

            "SELECT AVG(stars) as avg_stars, COUNT(*) as cnt, SUM(stars) as total "

            "FROM ratings WHERE to_user_id=?",

            (user_id,)

        )

        row = await cur.fetchone()

        

        if row and row["cnt"] > 0:

            avg_rating = round(row["avg_stars"], 2)

            await conn.execute(

                "UPDATE users SET average_rating=?, rating_count=?, total_rating_points=? WHERE tg_id=?",

                (avg_rating, row["cnt"], row["total"], user_id)

            )

        else:

            await conn.execute(

                "UPDATE users SET average_rating=0.0, rating_count=0, total_rating_points=0 WHERE tg_id=?",

                (user_id,)

            )

        await conn.commit()





async def get_user_ratings(user_id: int, limit: int = 10) -> list[dict]:

    """Получить последние отзывы о пользователе"""

    async with db() as conn:

        cur = await conn.execute(

            "SELECT r.*, u.display_name as from_display_name, u.username as from_username, r.created_at "

            "FROM ratings r "

            "LEFT JOIN users u ON u.tg_id = r.from_user_id "

            "WHERE r.to_user_id=? "

            "ORDER BY r.created_at DESC LIMIT ?",

            (user_id, limit)

        )

        rows = await cur.fetchall()

        return [dict(r) for r in rows]





async def check_can_rate(deal_id: int, from_user_id: int) -> bool:

    """Проверить может ли пользователь оставить отзыв"""

    async with db() as conn:

        cur = await conn.execute(

            "SELECT COUNT(*) as cnt FROM ratings WHERE deal_id=? AND from_user_id=?",

            (deal_id, from_user_id)

        )

        row = await cur.fetchone()

        return row["cnt"] == 0





# ==================== СТАТИСТИКА ПОЛЬЗОВАТЕЛЯ ====================



async def get_user_statistics(user_id: int) -> dict | None:

    """Получить полную статистику пользователя (пересчитывается на основе реальных сделок)"""

    async with db() as conn:

        cur = await conn.execute("SELECT * FROM users WHERE tg_id=?", (user_id,))

        user = await cur.fetchone()

        

        if not user:

            return None

        

        user_dict = dict(user)

        

        # Подсчитываем реальные сделки из БД одним запросом для оптимизации
        # Сделка считается "начатой", если она достигла статуса MATCHED или выше
        # Используем CASE WHEN для подсчета разных статусов в одном запросе
        # Включаем все статусы, кроме AWAITING_DEPOSIT и CONFIRMING (это еще не начатые сделки)
        cur = await conn.execute(
            """
            SELECT 
                COUNT(*) as total_deals,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) as cancelled_deals,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) as disputed_deals,
                SUM(CASE WHEN status IN (?, ?, ?) THEN 1 ELSE 0 END) as active_deals
            FROM deals
            WHERE (owner_id=? OR partner_id=?)
            AND status NOT IN (?, ?)
            """,
            (
                DealStatus.RELEASED,
                DealStatus.CANCELLED,
                DealStatus.DISPUTED,
                DealStatus.MATCHED, DealStatus.PAYMENT_PENDING, DealStatus.DISPUTED,
                user_id, user_id,
                DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING
            )
        )
        stats_row = await cur.fetchone()
        
        total_deals = stats_row["total_deals"] if stats_row and stats_row["total_deals"] else 0
        success_count = stats_row["success_count"] if stats_row and stats_row["success_count"] else 0
        cancelled_deals = stats_row["cancelled_deals"] if stats_row and stats_row["cancelled_deals"] else 0
        disputed_deals = stats_row["disputed_deals"] if stats_row and stats_row["disputed_deals"] else 0
        active_deals = stats_row["active_deals"] if stats_row and stats_row["active_deals"] else 0

        # Рассчитываем средний объём сделки
        cur = await conn.execute(
            "SELECT AVG(CAST(buy_amount AS REAL)) as avg_volume, "
            "COUNT(*) as completed_count "
            "FROM deals WHERE (owner_id=? OR partner_id=?) AND status=? AND buy_amount IS NOT NULL",
            (user_id, user_id, DealStatus.RELEASED)
        )
        volume_data = await cur.fetchone()

        avg_volume = volume_data["avg_volume"] if volume_data and volume_data["avg_volume"] else 0

        # Процент отмен
        cancel_rate = (cancelled_deals / total_deals * 100) if total_deals > 0 else 0

        # Процент успеха
        success_rate = (success_count / total_deals * 100) if total_deals > 0 else 0

        # Общий оборот (из поля пользователя или пересчитать из сделок)
        total_volume = Decimal(user_dict.get("total_volume_btc") or "0")

        # Если оборот в профиле 0, но есть успешные сделки, пересчитываем
        if total_volume == 0 and success_count > 0:
            cur = await conn.execute(
                "SELECT SUM(CAST(buy_amount AS REAL)) as total "
                "FROM deals "
                "WHERE (owner_id=? OR partner_id=?) AND status=? AND buy_amount IS NOT NULL",
                (user_id, user_id, DealStatus.RELEASED)
            )
            volume_sum_row = await cur.fetchone()
            if volume_sum_row and volume_sum_row["total"]:
                total_volume = Decimal(str(volume_sum_row["total"]))

        return {
            "success_count": success_count,
            "total_deals": total_deals,
            "cancelled_deals": cancelled_deals,
            "disputed_deals": disputed_deals,
            "active_deals": active_deals,
            "success_rate": round(success_rate, 1),
            "cancel_rate": round(cancel_rate, 1),
            "avg_volume": round(avg_volume, 8) if avg_volume else 0,
            "total_volume": str(total_volume),
            "total_commission_paid": user_dict.get("total_commission_paid") or "0",
            "average_rating": user_dict.get("average_rating") or 0,
            "rating_count": user_dict.get("rating_count") or 0,
            "response_time_avg": user_dict.get("response_time_avg") or 0,
        }





async def update_user_stats_on_deal_complete(deal_id: int):

    """Обновить статистику пользователей при завершении сделки"""

    d = await get_deal(deal_id)

    if not d:

        return

    

    buy_amount = Decimal(d.get("buy_amount") or d["max_btc"])

    owner_commission = Decimal(d.get("seller_net_loss") or "0")

    

    async with db() as conn:

        await conn.execute(

            "UPDATE users SET "

            "total_volume_btc = CAST(CAST(total_volume_btc AS REAL) + ? AS TEXT), "

            "total_commission_paid = CAST(CAST(total_commission_paid AS REAL) + ? AS TEXT) "

            "WHERE tg_id=?",

            (float(buy_amount), float(owner_commission), d["owner_id"])

        )

        

        if d.get("partner_id"):

            partner_commission = Decimal(d.get("buyer_extra") or "0")

            await conn.execute(

                "UPDATE users SET "

                "total_volume_btc = CAST(CAST(total_volume_btc AS REAL) + ? AS TEXT), "

                "total_commission_paid = CAST(CAST(total_commission_paid AS REAL) + ? AS TEXT) "

                "WHERE tg_id=?",

                (float(buy_amount), float(partner_commission), d["partner_id"])

            )

        

        await conn.commit()





async def update_user_stats_on_cancel(user_id: int):

    """Увеличить счётчик отменённых сделок"""

    async with db() as conn:

        await conn.execute(

            "UPDATE users SET cancelled_deals = cancelled_deals + 1 WHERE tg_id=?",

            (user_id,)

        )

        await conn.commit()





async def update_user_stats_on_dispute(user_id: int):

    """Увеличить счётчик споров"""

    async with db() as conn:

        await conn.execute(

            "UPDATE users SET disputed_deals = disputed_deals + 1 WHERE tg_id=?",

            (user_id,)

        )

        await conn.commit()

# ==================== ЕЖЕДНЕВНАЯ СТАТИСТИКА ====================



async def get_daily_stats(date_str: str = None) -> dict:

    """Получить статистику за конкретный день"""

    if not date_str:

        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    

    async with db() as conn:

        cur = await conn.execute("SELECT * FROM daily_stats WHERE date=?", (date_str,))

        row = await cur.fetchone()

        

        if row:

            return dict(row)

        

        return {

            "date": date_str,

            "total_deals": 0,

            "successful_deals": 0,

            "cancelled_deals": 0,

            "disputed_deals": 0,

            "total_volume_btc": "0",

            "total_commission_btc": "0",

            "active_users": 0,

            "new_users": 0

        }





async def update_daily_stats(date_str: str = None):

    """Обновить дневную статистику"""

    if not date_str:

        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    

    start_of_day = f"{date_str} 00:00:00"

    end_of_day = f"{date_str} 23:59:59"

    

    async with db() as conn:

        cur = await conn.execute(

            "SELECT "

            "COUNT(*) as total, "

            "SUM(CASE WHEN status=? THEN 1 ELSE 0 END) as successful, "

            "SUM(CASE WHEN status=? THEN 1 ELSE 0 END) as cancelled, "

            "SUM(CASE WHEN status=? THEN 1 ELSE 0 END) as disputed, "

            "SUM(CASE WHEN status=? AND buy_amount IS NOT NULL THEN CAST(buy_amount AS REAL) ELSE 0 END) as volume, "

            "SUM(CASE WHEN status=? AND platform_commission IS NOT NULL THEN CAST(platform_commission AS REAL) ELSE 0 END) as commission "

            "FROM deals WHERE created_at >= ? AND created_at <= ?",

            (DealStatus.RELEASED, DealStatus.CANCELLED, DealStatus.DISPUTED, 

             DealStatus.RELEASED, DealStatus.RELEASED, start_of_day, end_of_day)

        )

        deals_stats = await cur.fetchone()

        

        # Считаем активных пользователей

        cur = await conn.execute(

            "SELECT COUNT(DISTINCT owner_id) + COUNT(DISTINCT partner_id) as active "

            "FROM deals WHERE created_at >= ? AND created_at <= ?",

            (start_of_day, end_of_day)

        )

        active_row = await cur.fetchone()

        active_users = active_row["active"] if active_row else 0

        

        # Вставляем или обновляем

        await conn.execute(

            "INSERT INTO daily_stats (date, total_deals, successful_deals, cancelled_deals, disputed_deals, "

            "total_volume_btc, total_commission_btc, active_users, new_users) "

            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0) "

            "ON CONFLICT(date) DO UPDATE SET "

            "total_deals=excluded.total_deals, "

            "successful_deals=excluded.successful_deals, "

            "cancelled_deals=excluded.cancelled_deals, "

            "disputed_deals=excluded.disputed_deals, "

            "total_volume_btc=excluded.total_volume_btc, "

            "total_commission_btc=excluded.total_commission_btc, "

            "active_users=excluded.active_users",

            (date_str, 

             deals_stats["total"] or 0,

             deals_stats["successful"] or 0,

             deals_stats["cancelled"] or 0,

             deals_stats["disputed"] or 0,

             str(deals_stats["volume"] or 0),

             str(deals_stats["commission"] or 0),

             active_users)

        )

        await conn.commit()





async def get_period_stats(days: int = 7) -> dict:

    """Получить статистику за период"""

    end_date = datetime.utcnow()

    start_date = end_date - timedelta(days=days)

    

    start_str = start_date.strftime("%Y-%m-%d")

    end_str = end_date.strftime("%Y-%m-%d")

    

    async with db() as conn:

        cur = await conn.execute(

            "SELECT "

            "SUM(total_deals) as total, "

            "SUM(successful_deals) as successful, "

            "SUM(cancelled_deals) as cancelled, "

            "SUM(disputed_deals) as disputed, "

            "SUM(CAST(total_volume_btc AS REAL)) as volume, "

            "SUM(CAST(total_commission_btc AS REAL)) as commission, "

            "AVG(active_users) as avg_active "

            "FROM daily_stats WHERE date >= ? AND date <= ?",

            (start_str, end_str)

        )

        row = await cur.fetchone()

        

        if row:

            return {

                "period_days": days,

                "total_deals": row["total"] or 0,

                "successful_deals": row["successful"] or 0,

                "cancelled_deals": row["cancelled"] or 0,

                "disputed_deals": row["disputed"] or 0,

                "total_volume_btc": round(row["volume"] or 0, 8),

                "total_commission_btc": round(row["commission"] or 0, 8),

                "avg_active_users": round(row["avg_active"] or 0, 1),

                "avg_deals_per_day": round((row["total"] or 0) / days, 1)

            }

        

        return {}





async def get_problematic_deals() -> list[dict]:

    """Получить проблемные сделки"""

    now = datetime.utcnow()

    

    async with db() as conn:

        # Споры

        cur = await conn.execute(

            "SELECT *, 'DISPUTED' as issue_type FROM deals WHERE status=? ORDER BY created_at DESC LIMIT 10",

            (DealStatus.DISPUTED,)

        )

        disputed = await cur.fetchall()

        

        # Долгие сделки (в PAYMENT_PENDING > 1 часа)

        one_hour_ago = (now - timedelta(hours=1)).isoformat()

        cur = await conn.execute(

            "SELECT *, 'LONG_PENDING' as issue_type FROM deals "

            "WHERE status=? AND created_at < ? ORDER BY created_at LIMIT 10",

            (DealStatus.PAYMENT_PENDING, one_hour_ago)

        )

        long_pending = await cur.fetchall()

        

        all_issues = [dict(r) for r in disputed] + [dict(r) for r in long_pending]

        

        return all_issues


async def get_disputed_deals() -> list[dict]:
    """Получить все споры (DISPUTED статус)"""
    async with db() as conn:
        cur = await conn.execute(
            "SELECT * FROM deals WHERE status=? ORDER BY created_at DESC",
            (DealStatus.DISPUTED,)
        )
        rows = await cur.fetchall()
        # aiosqlite.Row уже поддерживает доступ по ключам
        return [dict(r) for r in rows]


async def get_active_deposits() -> list[dict]:
    """Получить все активные депозиты (сделки со статусом AWAITING_DEPOSIT, ESCROWED, CONFIRMING, ACTIVE, MATCHED, PAYMENT_PENDING)"""
    async with db() as conn:
        cur = await conn.execute(
            "SELECT * FROM deals WHERE status IN (?, ?, ?, ?, ?, ?) ORDER BY created_at DESC",
            (DealStatus.AWAITING_DEPOSIT, DealStatus.ESCROWED, DealStatus.CONFIRMING, 
             DealStatus.ACTIVE, DealStatus.MATCHED, DealStatus.PAYMENT_PENDING)
        )
        rows = await cur.fetchall()
        # aiosqlite.Row уже поддерживает доступ по ключам
        return [dict(r) for r in rows]


async def get_buyer_wallet(deal: dict) -> str | None:
    """
    Получить кошелек покупателя для сделки.
    Сначала проверяет buyer_wallet в сделке, если нет - берет из профиля покупателя.
    
    Args:
        deal: Словарь с данными сделки
        
    Returns:
        BTC кошелек покупателя или None
    """
    # Сначала проверяем buyer_wallet в сделке
    buyer_wallet = deal.get("buyer_wallet")
    if buyer_wallet and buyer_wallet.strip():
        return buyer_wallet.strip()
    
    # Если нет в сделке, определяем ID покупателя и берем из профиля
    buyer_id = None
    if (deal.get("side") or "").upper() in ("SELL", "GARANT"):
        # Для SELL и автогаранта BTC получает второй участник
        buyer_id = deal.get("partner_id")
    else:
        # Для BUY сделок покупатель - это owner_id
        buyer_id = deal.get("owner_id")
    
    if buyer_id:
        buyer_user = await get_user(buyer_id)
        if buyer_user and buyer_user.get("btc_wallet"):
            return buyer_user["btc_wallet"].strip()
    
    return None





# ==================== СОХРАНЁННЫЕ РЕКВИЗИТЫ ====================



async def get_saved_payment_details(user_id: int) -> list[dict]:

    """Получить все сохранённые реквизиты пользователя"""

    async with db() as conn:

        cur = await conn.execute(

            "SELECT * FROM saved_payment_details WHERE user_id=? ORDER BY created_at DESC LIMIT 3",

            (user_id,)

        )

        rows = await cur.fetchall()

        return [dict(r) for r in rows]





async def add_saved_payment_details(user_id: int, title: str, details: str) -> int:

    """Добавить новые реквизиты"""

    existing = await get_saved_payment_details(user_id)

    if len(existing) >= 3:

        raise ValueError("Максимум 3 сохранённых реквизита. Удалите старые.")

    

    async with db() as conn:

        cur = await conn.execute(

            "INSERT INTO saved_payment_details (user_id, title, details, created_at) VALUES (?, ?, ?, ?)",

            (user_id, title, details, datetime.utcnow().isoformat())

        )

        await conn.commit()

        return cur.lastrowid





async def delete_saved_payment_details(detail_id: int, user_id: int) -> bool:

    """Удалить сохранённые реквизиты"""

    async with db() as conn:

        cur = await conn.execute(

            "DELETE FROM saved_payment_details WHERE id=? AND user_id=?",

            (detail_id, user_id)

        )

        await conn.commit()

        return cur.rowcount > 0





async def get_payment_detail_by_id(detail_id: int) -> dict | None:

    """Получить реквизит по ID"""

    async with db() as conn:

        cur = await conn.execute(

            "SELECT * FROM saved_payment_details WHERE id=?",

            (detail_id,)

        )

        row = await cur.fetchone()

        return dict(row) if row else None





# ==================== ИЗБРАННЫЕ КОНТРАГЕНТЫ ====================



async def get_favorite_partners(user_id: int) -> list[dict]:

    """Получить всех избранных контрагентов"""

    async with db() as conn:

        cur = await conn.execute(

            "SELECT f.*, u.username, u.display_name, u.average_rating, u.success_count, u.total_deals "

            "FROM favorite_partners f "

            "LEFT JOIN users u ON u.tg_id = f.partner_id "

            "WHERE f.user_id=? ORDER BY f.added_at DESC",

            (user_id,)

        )

        rows = await cur.fetchall()

        return [dict(r) for r in rows]





async def add_favorite_partner(user_id: int, partner_id: int, note: str = None) -> bool:

    """Добавить контрагента в избранное"""

    if user_id == partner_id:

        raise ValueError("Нельзя добавить себя в избранное")

    

    async with db() as conn:

        try:

            await conn.execute(

                "INSERT INTO favorite_partners (user_id, partner_id, note, added_at) VALUES (?, ?, ?, ?)",

                (user_id, partner_id, note, datetime.utcnow().isoformat())

            )

            await conn.commit()

            return True

        except Exception:

            return False





async def remove_favorite_partner(user_id: int, partner_id: int) -> bool:

    """Удалить контрагента из избранного"""

    async with db() as conn:

        cur = await conn.execute(

            "DELETE FROM favorite_partners WHERE user_id=? AND partner_id=?",

            (user_id, partner_id)

        )

        await conn.commit()

        return cur.rowcount > 0





async def is_favorite(user_id: int, partner_id: int) -> bool:

    """Проверить в избранном ли контрагент"""

    async with db() as conn:

        cur = await conn.execute(

            "SELECT COUNT(*) as cnt FROM favorite_partners WHERE user_id=? AND partner_id=?",

            (user_id, partner_id)

        )

        row = await cur.fetchone()

        return row["cnt"] > 0





async def update_favorite_note(user_id: int, partner_id: int, note: str):

    """Обновить заметку об избранном контрагенте"""

    async with db() as conn:

        await conn.execute(

            "UPDATE favorite_partners SET note=? WHERE user_id=? AND partner_id=?",

            (note, user_id, partner_id)

        )

        await conn.commit()





# ==================== ЧАТ ====================



async def add_deal_message(deal_id: int, from_user_id: int, message_text: str, is_system: bool = False):

    """Добавить сообщение в чат сделки"""

    d = await get_deal(deal_id)

    if not d:

        raise ValueError("Сделка не найдена")

    

    allowed_statuses = [DealStatus.MATCHED, DealStatus.PAYMENT_PENDING, DealStatus.DISPUTED]

    

    if d["status"] not in allowed_statuses and not is_system:

        raise ValueError("Чат доступен только во время активной сделки")

    

    if not is_system:

        allowed_users = {d["owner_id"], d.get("partner_id")}

        if from_user_id not in allowed_users:

            raise ValueError("Только участники сделки могут писать в чат")

        

        # Проверяем rate limit

        if not await check_message_cooldown(from_user_id):

            raise ValueError("Слишком часто. Подождите 3 секунды.")

    

    if len(message_text) > 1000:

        raise ValueError("Сообщение слишком длинное (макс. 1000 символов)")

    

    # Фильтр запрещённых слов

    message_lower = message_text.lower()

    has_forbidden = any(word in message_lower for word in FORBIDDEN_WORDS)

    

    if has_forbidden and not is_system:

        # Уведомляем админов о подозрительном сообщении

        from aiogram import Bot

        

        targets = list(ADMIN_IDS) if ADMIN_IDS else []

        if ADMIN_CHAT_ID:

            targets.append(ADMIN_CHAT_ID)

        

        for admin_id in targets:

            try:

                from utils import create_bot
                bot = create_bot(BOT_TOKEN)

                await bot.send_message(

                    admin_id,

                    f"⚠️ [ПОДОЗРИТЕЛЬНОЕ СООБЩЕНИЕ]\n"

                    f"Сделка #{deal_id}\n"

                    f"От: {from_user_id}\n"

                    f"Текст: {message_text[:100]}"

                )

                await bot.session.close()

            except Exception:

                pass

    

    async with db() as conn:

        await conn.execute(

            "INSERT INTO deal_messages (deal_id, from_user_id, message_text, created_at, is_system) "

            "VALUES (?, ?, ?, ?, ?)",

            (deal_id, from_user_id, message_text, datetime.utcnow().isoformat(), is_system)

        )

        await conn.commit()









async def get_deal_messages(deal_id: int, limit: int = 50) -> list[dict]:

    """Получить последние сообщения чата"""

    async with db() as conn:

        cur = await conn.execute(

            "SELECT dm.*, u.username "

            "FROM deal_messages dm "

            "LEFT JOIN users u ON u.tg_id = dm.from_user_id "

            "WHERE dm.deal_id=? "

            "ORDER BY dm.created_at DESC LIMIT ?",

            (deal_id, limit)

        )

        rows = await cur.fetchall()

        return [dict(r) for r in reversed(rows)]





async def can_access_chat(user_id: int, deal_id: int) -> bool:

    """Проверить может ли пользователь получить доступ к чату"""

    d = await get_deal(deal_id)

    if not d:

        return False

    

    if user_id in ADMIN_IDS:

        return True

    

    allowed_users = {d["owner_id"], d.get("partner_id")}

    return user_id in allowed_users





async def check_message_cooldown(user_id: int) -> bool:

    """Проверка rate limit для сообщений"""

    now = time.time()

    last_time = user_last_message_time[user_id]

    

    if now - last_time < MESSAGE_COOLDOWN:

        return False

    

    user_last_message_time[user_id] = now

    return True





async def send_system_message(deal_id: int, message: str):

    """Отправить системное сообщение в чат"""

    try:

        await add_deal_message(deal_id, 0, message, is_system=True)

    except Exception:

        pass





async def get_chat_participants(deal_id: int) -> dict | None:

    """Получить участников чата"""

    d = await get_deal(deal_id)

    if not d:

        return None

    

    return {

        "owner_id": d["owner_id"],

        "partner_id": d.get("partner_id"),

        "side": d["side"]

    }
