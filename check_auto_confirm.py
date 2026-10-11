"""
Скрипт для проверки настройки автоподтверждения депозитов
"""
import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path)

print("=" * 60)
print("ПРОВЕРКА НАСТРОЙКИ АВТОПОДТВЕРЖДЕНИЯ ДЕПОЗИТОВ")
print("=" * 60)

# Проверка переменных окружения
print("\n1. Проверка переменных окружения:")
print("-" * 60)

DEPOSIT_BOT_TOKEN = os.getenv("DEPOSIT_BOT_TOKEN")
DEPOSIT_NOTIFY_CHAT_ID = os.getenv("DEPOSIT_NOTIFY_CHAT_ID", "0")
TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")
SOURCE_BOT_USERNAME = os.getenv("SOURCE_BOT_USERNAME", "bmbsilaKR_bot")

print(f"   DEPOSIT_BOT_TOKEN: {'[OK] Установлен' if DEPOSIT_BOT_TOKEN else '[X] НЕ УСТАНОВЛЕН'}")
print(f"   DEPOSIT_NOTIFY_CHAT_ID: {DEPOSIT_NOTIFY_CHAT_ID if DEPOSIT_NOTIFY_CHAT_ID != '0' else '[X] НЕ УСТАНОВЛЕН'}")
print(f"   TG_API_ID: {'[OK] Установлен' if TG_API_ID else '[X] НЕ УСТАНОВЛЕН'}")
print(f"   TG_API_HASH: {'[OK] Установлен' if TG_API_HASH else '[X] НЕ УСТАНОВЛЕН'}")
print(f"   SOURCE_BOT_USERNAME: {SOURCE_BOT_USERNAME}")

# Проверка процессов
print("\n2. Проверка запущенных процессов:")
print("-" * 60)

import subprocess
try:
    result = subprocess.run(
        ['powershell', '-Command', 'Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    if result.stdout.strip():
        print("   [OK] Процессы Python запущены:")
        for line in result.stdout.strip().split('\n')[2:]:  # Пропускаем заголовки
            if line.strip():
                print(f"      {line.strip()}")
    else:
        print("   [X] Процессы Python не найдены")
except Exception as e:
    print(f"   ⚠️ Не удалось проверить процессы: {e}")

# Проверка файлов сессий
print("\n3. Проверка файлов сессий:")
print("-" * 60)

forwarder_session = BASE_DIR / "forwarder_session.session"
if forwarder_session.exists():
    print(f"   [OK] Сессия forwarder найдена: {forwarder_session}")
else:
    print(f"   [!] Сессия forwarder не найдена (нужно запустить forwarder.py для авторизации)")

# Проверка способов автоподтверждения
print("\n4. Доступные способы автоподтверждения:")
print("-" * 60)

method1_ok = bool(DEPOSIT_BOT_TOKEN)
method2_ok = bool(DEPOSIT_NOTIFY_CHAT_ID and DEPOSIT_NOTIFY_CHAT_ID != "0" and TG_API_ID and TG_API_HASH)

if method1_ok:
    print("   [OK] Способ 1: Прямое получение сообщений от @bmbsilaKR_bot")
    print("      (через DEPOSIT_BOT_TOKEN в main.py)")
    print("      Требуется: DEPOSIT_BOT_TOKEN")
else:
    print("   [X] Способ 1: НЕ ДОСТУПЕН")
    print("      Требуется: DEPOSIT_BOT_TOKEN")

if method2_ok:
    print("\n   [OK] Способ 2: Получение сообщений через канал/группу")
    print("      (через forwarder.py + DEPOSIT_NOTIFY_CHAT_ID)")
    print("      Требуется: DEPOSIT_NOTIFY_CHAT_ID, TG_API_ID, TG_API_HASH")
    print("      Требуется запуск: forwarder.py")
else:
    print("\n   [X] Способ 2: НЕ ДОСТУПЕН")
    print("      Требуется: DEPOSIT_NOTIFY_CHAT_ID, TG_API_ID, TG_API_HASH")

# Рекомендации
print("\n5. Рекомендации:")
print("-" * 60)

if not method1_ok and not method2_ok:
    print("   [X] Автоподтверждение НЕ НАСТРОЕНО!")
    print("\n   Для настройки:")
    if not DEPOSIT_BOT_TOKEN:
        print("   1. Установите DEPOSIT_BOT_TOKEN в .env (токен бота для получения сообщений)")
    if not DEPOSIT_NOTIFY_CHAT_ID or DEPOSIT_NOTIFY_CHAT_ID == "0":
        print("   2. Установите DEPOSIT_NOTIFY_CHAT_ID в .env (ID канала/группы)")
    if not TG_API_ID or not TG_API_HASH:
        print("   3. Установите TG_API_ID и TG_API_HASH в .env (для forwarder.py)")
elif method1_ok:
    print("   [OK] Способ 1 настроен - автоподтверждение должно работать")
    print("   Убедитесь, что бот с DEPOSIT_BOT_TOKEN запущен")
elif method2_ok:
    print("   [OK] Способ 2 настроен - автоподтверждение должно работать")
    print("   Убедитесь, что:")
    print("   - forwarder.py запущен и авторизован")
    print("   - Основной бот запущен")
    print(f"   - Оба бота добавлены в канал/группу с ID: {DEPOSIT_NOTIFY_CHAT_ID}")

print("\n" + "=" * 60)


"""
import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path)

print("=" * 60)
print("ПРОВЕРКА НАСТРОЙКИ АВТОПОДТВЕРЖДЕНИЯ ДЕПОЗИТОВ")
print("=" * 60)

# Проверка переменных окружения
print("\n1. Проверка переменных окружения:")
print("-" * 60)

DEPOSIT_BOT_TOKEN = os.getenv("DEPOSIT_BOT_TOKEN")
DEPOSIT_NOTIFY_CHAT_ID = os.getenv("DEPOSIT_NOTIFY_CHAT_ID", "0")
TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")
SOURCE_BOT_USERNAME = os.getenv("SOURCE_BOT_USERNAME", "bmbsilaKR_bot")

print(f"   DEPOSIT_BOT_TOKEN: {'[OK] Установлен' if DEPOSIT_BOT_TOKEN else '[X] НЕ УСТАНОВЛЕН'}")
print(f"   DEPOSIT_NOTIFY_CHAT_ID: {DEPOSIT_NOTIFY_CHAT_ID if DEPOSIT_NOTIFY_CHAT_ID != '0' else '[X] НЕ УСТАНОВЛЕН'}")
print(f"   TG_API_ID: {'[OK] Установлен' if TG_API_ID else '[X] НЕ УСТАНОВЛЕН'}")
print(f"   TG_API_HASH: {'[OK] Установлен' if TG_API_HASH else '[X] НЕ УСТАНОВЛЕН'}")
print(f"   SOURCE_BOT_USERNAME: {SOURCE_BOT_USERNAME}")

# Проверка процессов
print("\n2. Проверка запущенных процессов:")
print("-" * 60)

import subprocess
try:
    result = subprocess.run(
        ['powershell', '-Command', 'Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    if result.stdout.strip():
        print("   [OK] Процессы Python запущены:")
        for line in result.stdout.strip().split('\n')[2:]:  # Пропускаем заголовки
            if line.strip():
                print(f"      {line.strip()}")
    else:
        print("   [X] Процессы Python не найдены")
except Exception as e:
    print(f"   ⚠️ Не удалось проверить процессы: {e}")

# Проверка файлов сессий
print("\n3. Проверка файлов сессий:")
print("-" * 60)

forwarder_session = BASE_DIR / "forwarder_session.session"
if forwarder_session.exists():
    print(f"   [OK] Сессия forwarder найдена: {forwarder_session}")
else:
    print(f"   [!] Сессия forwarder не найдена (нужно запустить forwarder.py для авторизации)")

# Проверка способов автоподтверждения
print("\n4. Доступные способы автоподтверждения:")
print("-" * 60)

method1_ok = bool(DEPOSIT_BOT_TOKEN)
method2_ok = bool(DEPOSIT_NOTIFY_CHAT_ID and DEPOSIT_NOTIFY_CHAT_ID != "0" and TG_API_ID and TG_API_HASH)

if method1_ok:
    print("   [OK] Способ 1: Прямое получение сообщений от @bmbsilaKR_bot")
    print("      (через DEPOSIT_BOT_TOKEN в main.py)")
    print("      Требуется: DEPOSIT_BOT_TOKEN")
else:
    print("   [X] Способ 1: НЕ ДОСТУПЕН")
    print("      Требуется: DEPOSIT_BOT_TOKEN")

if method2_ok:
    print("\n   [OK] Способ 2: Получение сообщений через канал/группу")
    print("      (через forwarder.py + DEPOSIT_NOTIFY_CHAT_ID)")
    print("      Требуется: DEPOSIT_NOTIFY_CHAT_ID, TG_API_ID, TG_API_HASH")
    print("      Требуется запуск: forwarder.py")
else:
    print("\n   [X] Способ 2: НЕ ДОСТУПЕН")
    print("      Требуется: DEPOSIT_NOTIFY_CHAT_ID, TG_API_ID, TG_API_HASH")

# Рекомендации
print("\n5. Рекомендации:")
print("-" * 60)

if not method1_ok and not method2_ok:
    print("   [X] Автоподтверждение НЕ НАСТРОЕНО!")
    print("\n   Для настройки:")
    if not DEPOSIT_BOT_TOKEN:
        print("   1. Установите DEPOSIT_BOT_TOKEN в .env (токен бота для получения сообщений)")
    if not DEPOSIT_NOTIFY_CHAT_ID or DEPOSIT_NOTIFY_CHAT_ID == "0":
        print("   2. Установите DEPOSIT_NOTIFY_CHAT_ID в .env (ID канала/группы)")
    if not TG_API_ID or not TG_API_HASH:
        print("   3. Установите TG_API_ID и TG_API_HASH в .env (для forwarder.py)")
elif method1_ok:
    print("   [OK] Способ 1 настроен - автоподтверждение должно работать")
    print("   Убедитесь, что бот с DEPOSIT_BOT_TOKEN запущен")
elif method2_ok:
    print("   [OK] Способ 2 настроен - автоподтверждение должно работать")
    print("   Убедитесь, что:")
    print("   - forwarder.py запущен и авторизован")
    print("   - Основной бот запущен")
    print(f"   - Оба бота добавлены в канал/группу с ID: {DEPOSIT_NOTIFY_CHAT_ID}")

print("\n" + "=" * 60)

