"""
Конфигурация бота
"""
import os
from pathlib import Path
from decimal import Decimal
from dotenv import load_dotenv

# Получаем абсолютный путь к директории проекта
BASE_DIR = Path(__file__).parent.absolute()

# Загружаем .env из директории проекта
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path)

# Токен бота
import sys

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("ОШИБКА: BOT_TOKEN не указан в .env файле!")
    sys.exit(1)

# Токен второго бота для подтверждения депозитов (опционально)
DEPOSIT_BOT_TOKEN = os.getenv("DEPOSIT_BOT_TOKEN")

# ID чата/канала, где приходят уведомления о депозитах (парсит основной бот)
DEPOSIT_NOTIFY_CHAT_ID = int(os.getenv("DEPOSIT_NOTIFY_CHAT_ID", "0")) or None

# Прокси для Telegram API (VPN часто даёт http://127.0.0.1:10801 — приложение Telegram его использует, Python нет)
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "http://127.0.0.1:10801").strip() or None

# Токен бота @bmbsilaKR_bot для автовывода и возврата депозитов
WITHDRAW_BOT_TOKEN = os.getenv("WITHDRAW_BOT_TOKEN")
WITHDRAW_BOT_USERNAME = "@bmbsilaKR_bot"  # Username бота для работы с выводами

# Админы
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip().isdigit()}

# Доступ к продаже (минимум успешных сделок) и исключения
SELL_ACCESS_MIN_SUCCESS = int(os.getenv("SELL_ACCESS_MIN_SUCCESS", "100"))
SELL_ACCESS_ALLOW_IDS_STR = os.getenv("SELL_ACCESS_ALLOW_IDS", "")
SELL_ACCESS_ALLOW_IDS = {int(x.strip()) for x in SELL_ACCESS_ALLOW_IDS_STR.split(",") if x.strip().isdigit()}

# Чат для уведомлений админов
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0")) if os.getenv("ADMIN_CHAT_ID") else None

# Публичный канал/чат для публикации заявок
PUBLIC_REQUESTS_CHAT_ID = int(os.getenv("PUBLIC_REQUESTS_CHAT_ID", "0")) if os.getenv("PUBLIC_REQUESTS_CHAT_ID") else None

# Эскроу адрес BTC
ESCROW_BTC_ADDRESS = os.getenv("ESCROW_BTC_ADDRESS", "")
if not ESCROW_BTC_ADDRESS:
    print("ОШИБКА: ESCROW_BTC_ADDRESS не указан в .env файле!")
    sys.exit(1)

# Время жизни заявки (часы)
TTL_HOURS = int(os.getenv("TTL_HOURS", "5"))

# Время на оплату после начала сделки (минуты)
PAYMENT_TIMEOUT_MINUTES = int(os.getenv("PAYMENT_TIMEOUT_MINUTES", "15"))

# Путь к БД (абсолютный путь)
DB_PATH = str(BASE_DIR / "p2p_bot.db")

# Комиссии
BUYER_COMMISSION_RATE = Decimal("0.00")    # ВРЕМЕННО: 0% для старта продукта (покупатель получает 100%)
CANCELLATION_FEE = Decimal("0.015")        # 1.5% штраф при отмене
# Устаревшие константы (для обратной совместимости)
SELLER_COMMISSION_RATE = Decimal("0")      # Продавец больше не платит комиссию
BUYER_EXTRA_RATE = Decimal("0")            # Покупатель больше не доплачивает сверху

# Ограничения чата
MESSAGE_COOLDOWN = 3  # секунд между сообщениями

# Запрещённые слова в чате
FORBIDDEN_WORDS = ['мошенник', 'скам', 'обман', 'кидала', 'лох']

# Курс BTC к рублям (можно обновлять через API или вручную)
# По умолчанию используется фиксированный курс, можно заменить на API
BTC_TO_RUB_RATE = Decimal(os.getenv("BTC_TO_RUB_RATE", "6500000"))  # Пример: 6.5 млн рублей за 1 BTC


# Статусы сделок
class DealStatus:
    AWAITING_DEPOSIT = "AwaitingDeposit"      # Ожидает депозита
    CONFIRMING = "Confirming"                  # Подтверждение депозита
    ESCROWED = "Escrowed"                      # Депозит подтверждён
    ACTIVE = "Active"                          # Опубликована
    MATCHED = "Matched"                        # Найден контрагент
    PAYMENT_PENDING = "PaymentPending"         # Ожидает оплаты
    RELEASED = "Released"                      # Успешно завершена
    CANCELLED = "Cancelled"                    # Отменена
    DISPUTED = "Disputed"                      # Спор


# Тексты для пользователя
HELP_TEXT = (
    "📞 Связь любым вопросам\n\n\n"
    "👨‍⚖️ admin: @FogoMy\n"
    "🕵️ admin: @K0_T_1K"
)

LIMITS_PROMPT = (
    "✨ <b>Создание заявки на продажу BTC</b> ✨\n\n"
    "📊 <b>Выберите лимиты продажи (мин — макс в BTC) или укажите одну сумму.</b>\n\n"
    "💡 <b>Как указать сумму:</b>\n\n"
    "📝 <b>Примеры:</b>\n"
    "• <code>0.005</code> — одна сумма\n"
    "• <code>0.001 - 0.10</code> или <code>0.001-0.10</code> — диапазон\n\n"
    "⚠️ <b>Важно:</b> Укажите сумму в BTC (например: 0.001 или 0.001 - 0.10)"
)

AMOUNT_PROMPT_TPL = (
    "✨ <b>Укажите сумму BTC для покупки</b> ✨\n\n"
    "💵 <b>Диапазон:</b> от {min_btc} до {max_btc} BTC\n\n"
    "📝 <b>Пример:</b> <code>0.005</code>"
)