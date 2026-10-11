#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Скрипт для запуска обоих ботов и поддержания их работы"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("=" * 60)
print("ЗАПУСК БОТОВ")
print("=" * 60)

# Останавливаем старые процессы
print("\nОстанавливаем старые процессы...")
try:
    if sys.platform == 'win32':
        subprocess.run(["taskkill", "/F", "/IM", "python.exe"], 
                      capture_output=True, check=False)
    else:
        subprocess.run(["pkill", "-f", "main.py"], check=False)
        subprocess.run(["pkill", "-f", "forwarder.py"], check=False)
except:
    pass

time.sleep(2)

# Запускаем основной бот
print("\nЗапускаю основной бот (main.py)...")
main_bot = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1
)

time.sleep(3)

# Запускаем forwarder
print("Запускаю forwarder (forwarder.py)...")
forwarder = subprocess.Popen(
    [sys.executable, "forwarder.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1
)

time.sleep(5)

# Проверяем статус
print("\n" + "=" * 60)
print("СТАТУС БОТОВ")
print("=" * 60)

main_running = main_bot.poll() is None
forwarder_running = forwarder.poll() is None

print(f"Основной бот: {'✅ РАБОТАЕТ' if main_running else '❌ ОСТАНОВЛЕН'}")
print(f"Forwarder: {'✅ РАБОТАЕТ' if forwarder_running else '❌ ОСТАНОВЛЕН'}")

if not main_running:
    print("\nОшибки основного бота:")
    try:
        stdout, stderr = main_bot.communicate(timeout=1)
        if stderr:
            print(stderr[-500:])
    except:
        pass

if not forwarder_running:
    print("\nОшибки forwarder:")
    try:
        stdout, stderr = forwarder.communicate(timeout=1)
        if stderr:
            print(stderr[-500:])
    except:
        pass

if main_running and forwarder_running:
    print("\n✅✅✅ ОБА БОТА ЗАПУЩЕНЫ И РАБОТАЮТ!")
    print("\nБоты работают в фоне.")
    print("Проверьте bot.log и forwarder.log для деталей.")
    print("\nНажмите Ctrl+C для остановки...")
    
    try:
        # Держим скрипт запущенным
        while True:
            if main_bot.poll() is not None:
                print("\n⚠️ Основной бот остановился!")
                break
            if forwarder.poll() is not None:
                print("\n⚠️ Forwarder остановился!")
                break
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n\nОстанавливаю ботов...")
        main_bot.terminate()
        forwarder.terminate()
        time.sleep(2)
        main_bot.kill()
        forwarder.kill()
        print("Боты остановлены.")
else:
    print("\n❌ Не удалось запустить ботов. Проверьте логи.")
    sys.exit(1)





#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Скрипт для запуска обоих ботов и поддержания их работы"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("=" * 60)
print("ЗАПУСК БОТОВ")
print("=" * 60)

# Останавливаем старые процессы
print("\nОстанавливаем старые процессы...")
try:
    if sys.platform == 'win32':
        subprocess.run(["taskkill", "/F", "/IM", "python.exe"], 
                      capture_output=True, check=False)
    else:
        subprocess.run(["pkill", "-f", "main.py"], check=False)
        subprocess.run(["pkill", "-f", "forwarder.py"], check=False)
except:
    pass

time.sleep(2)

# Запускаем основной бот
print("\nЗапускаю основной бот (main.py)...")
main_bot = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1
)

time.sleep(3)

# Запускаем forwarder
print("Запускаю forwarder (forwarder.py)...")
forwarder = subprocess.Popen(
    [sys.executable, "forwarder.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1
)

time.sleep(5)

# Проверяем статус
print("\n" + "=" * 60)
print("СТАТУС БОТОВ")
print("=" * 60)

main_running = main_bot.poll() is None
forwarder_running = forwarder.poll() is None

print(f"Основной бот: {'✅ РАБОТАЕТ' if main_running else '❌ ОСТАНОВЛЕН'}")
print(f"Forwarder: {'✅ РАБОТАЕТ' if forwarder_running else '❌ ОСТАНОВЛЕН'}")

if not main_running:
    print("\nОшибки основного бота:")
    try:
        stdout, stderr = main_bot.communicate(timeout=1)
        if stderr:
            print(stderr[-500:])
    except:
        pass

if not forwarder_running:
    print("\nОшибки forwarder:")
    try:
        stdout, stderr = forwarder.communicate(timeout=1)
        if stderr:
            print(stderr[-500:])
    except:
        pass

if main_running and forwarder_running:
    print("\n✅✅✅ ОБА БОТА ЗАПУЩЕНЫ И РАБОТАЮТ!")
    print("\nБоты работают в фоне.")
    print("Проверьте bot.log и forwarder.log для деталей.")
    print("\nНажмите Ctrl+C для остановки...")
    
    try:
        # Держим скрипт запущенным
        while True:
            if main_bot.poll() is not None:
                print("\n⚠️ Основной бот остановился!")
                break
            if forwarder.poll() is not None:
                print("\n⚠️ Forwarder остановился!")
                break
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n\nОстанавливаю ботов...")
        main_bot.terminate()
        forwarder.terminate()
        time.sleep(2)
        main_bot.kill()
        forwarder.kill()
        print("Боты остановлены.")
else:
    print("\n❌ Не удалось запустить ботов. Проверьте логи.")
    sys.exit(1)






