"""
Скрипт для проверки настройки автоподтверждения депозитов
"""
import os
from dotenv import load_dotenv
from config import DEPOSIT_NOTIFY_CHAT_ID, BOT_TOKEN

load_dotenv()

print("=" * 50)
print("ПРОВЕРКА НАСТРОЙКИ АВТОПОДТВЕРЖДЕНИЯ ДЕПОЗИТОВ")
print("=" * 50)

# Проверка переменных окружения
print("\n1. Проверка переменных окружения:")
print(f"   DEPOSIT_NOTIFY_CHAT_ID: {DEPOSIT_NOTIFY_CHAT_ID}")
print(f"   BOT_TOKEN: {'Установлен' if BOT_TOKEN else '❌ НЕ УСТАНОВЛЕН'}")

# Проверка Pyrogram переменных
TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")
SOURCE_BOT_USERNAME = os.getenv("SOURCE_BOT_USERNAME", "bmbsilaKR_bot")

print(f"   TG_API_ID: {'Установлен' if TG_API_ID else '❌ НЕ УСТАНОВЛЕН'}")
print(f"   TG_API_HASH: {'Установлен' if TG_API_HASH else '❌ НЕ УСТАНОВЛЕН'}")
print(f"   SOURCE_BOT_USERNAME: {SOURCE_BOT_USERNAME}")

# Проверка ID чата
print("\n2. Проверка ID чата:")
if DEPOSIT_NOTIFY_CHAT_ID:
    print(f"   ✅ ID чата установлен: {DEPOSIT_NOTIFY_CHAT_ID}")
    if str(DEPOSIT_NOTIFY_CHAT_ID).startswith("-100"):
        print("   ✅ Формат ID правильный (канал)")
    elif str(DEPOSIT_NOTIFY_CHAT_ID).startswith("-"):
        print("   ✅ Формат ID правильный (группа)")
    else:
        print("   ⚠️  ID выглядит как личный чат")
else:
    print("   ❌ ID чата НЕ установлен!")

print("\n3. Рекомендации:")
print("   - Убедитесь, что мостик (forwarder.py) запущен")
print("   - Убедитесь, что основной бот добавлен в чат как администратор")
print("   - Убедитесь, что ваш аккаунт (для мостика) добавлен в чат")
print("   - Проверьте логи бота на наличие сообщений из чата")

print("\n" + "=" * 50)


