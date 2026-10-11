#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Скрипт для запуска ботов с отладкой"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("=" * 60)
print("ЗАПУСК БОТОВ С ОТЛАДКОЙ")
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

# Запускаем основной бот с выводом ошибок
print("\nЗапускаю основной бот (main.py)...")
try:
    main_bot = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    print(f"Main bot PID: {main_bot.pid}")
except Exception as e:
    print(f"Ошибка запуска main.py: {e}")
    sys.exit(1)

time.sleep(3)

# Запускаем forwarder
print("Запускаю forwarder (forwarder.py)...")
try:
    forwarder = subprocess.Popen(
        [sys.executable, "forwarder.py"],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    print(f"Forwarder PID: {forwarder.pid}")
except Exception as e:
    print(f"Ошибка запуска forwarder.py: {e}")
    main_bot.terminate()
    sys.exit(1)

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
        stdout, _ = main_bot.communicate(timeout=2)
        if stdout:
            print(stdout[-1000:])
    except:
        pass

if not forwarder_running:
    print("\nОшибки forwarder:")
    try:
        stdout, _ = forwarder.communicate(timeout=2)
        if stdout:
            print(stdout[-1000:])
    except:
        pass

if main_running and forwarder_running:
    print("\n✅✅✅ ОБА БОТА ЗАПУЩЕНЫ И РАБОТАЮТ!")
    print("\nБоты работают в фоне.")
    print("Проверьте bot.log и forwarder.log для деталей.")
    print("\nНажмите Ctrl+C для остановки...")
    
    try:
        # Держим скрипт запущенным и читаем вывод
        import select
        while True:
            if main_bot.poll() is not None:
                print("\n⚠️ Основной бот остановился!")
                stdout, _ = main_bot.communicate()
                if stdout:
                    print("Вывод основного бота:")
                    print(stdout[-500:])
                break
            if forwarder.poll() is not None:
                print("\n⚠️ Forwarder остановился!")
                stdout, _ = forwarder.communicate()
                if stdout:
                    print("Вывод forwarder:")
                    print(stdout[-500:])
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
    print("\n❌ Не удалось запустить ботов. Проверьте логи выше.")
    sys.exit(1)





#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Скрипт для запуска ботов с отладкой"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("=" * 60)
print("ЗАПУСК БОТОВ С ОТЛАДКОЙ")
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

# Запускаем основной бот с выводом ошибок
print("\nЗапускаю основной бот (main.py)...")
try:
    main_bot = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    print(f"Main bot PID: {main_bot.pid}")
except Exception as e:
    print(f"Ошибка запуска main.py: {e}")
    sys.exit(1)

time.sleep(3)

# Запускаем forwarder
print("Запускаю forwarder (forwarder.py)...")
try:
    forwarder = subprocess.Popen(
        [sys.executable, "forwarder.py"],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    print(f"Forwarder PID: {forwarder.pid}")
except Exception as e:
    print(f"Ошибка запуска forwarder.py: {e}")
    main_bot.terminate()
    sys.exit(1)

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
        stdout, _ = main_bot.communicate(timeout=2)
        if stdout:
            print(stdout[-1000:])
    except:
        pass

if not forwarder_running:
    print("\nОшибки forwarder:")
    try:
        stdout, _ = forwarder.communicate(timeout=2)
        if stdout:
            print(stdout[-1000:])
    except:
        pass

if main_running and forwarder_running:
    print("\n✅✅✅ ОБА БОТА ЗАПУЩЕНЫ И РАБОТАЮТ!")
    print("\nБоты работают в фоне.")
    print("Проверьте bot.log и forwarder.log для деталей.")
    print("\nНажмите Ctrl+C для остановки...")
    
    try:
        # Держим скрипт запущенным и читаем вывод
        import select
        while True:
            if main_bot.poll() is not None:
                print("\n⚠️ Основной бот остановился!")
                stdout, _ = main_bot.communicate()
                if stdout:
                    print("Вывод основного бота:")
                    print(stdout[-500:])
                break
            if forwarder.poll() is not None:
                print("\n⚠️ Forwarder остановился!")
                stdout, _ = forwarder.communicate()
                if stdout:
                    print("Вывод forwarder:")
                    print(stdout[-500:])
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
    print("\n❌ Не удалось запустить ботов. Проверьте логи выше.")
    sys.exit(1)






