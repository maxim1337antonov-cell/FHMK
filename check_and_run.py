#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Проверка и запуск ботов"""
import sys
import os
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("=" * 60)
print("ПРОВЕРКА И ЗАПУСК БОТОВ")
print("=" * 60)

# Проверка импортов
print("\n1. Проверка импортов...")
try:
    print("   Проверка main.py...")
    import main
    print("   ✅ main.py импортирован успешно")
except Exception as e:
    print(f"   ❌ Ошибка импорта main.py: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("   Проверка forwarder.py...")
    import forwarder
    print("   ✅ forwarder.py импортирован успешно")
except Exception as e:
    print(f"   ❌ Ошибка импорта forwarder.py: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Останавливаем старые процессы
print("\n2. Остановка старых процессов...")
try:
    if sys.platform == 'win32':
        subprocess.run(["taskkill", "/F", "/IM", "python.exe"], 
                      capture_output=True, check=False)
    time.sleep(2)
except:
    pass

# Запускаем ботов
print("\n3. Запуск ботов...")
print("   Запускаю main.py...")
main_proc = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

time.sleep(3)

print("   Запускаю forwarder.py...")
forwarder_proc = subprocess.Popen(
    [sys.executable, "forwarder.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

time.sleep(5)

# Проверяем статус
print("\n4. Проверка статуса...")
main_running = main_proc.poll() is None
forwarder_running = forwarder_proc.poll() is None

print(f"   Main bot: {'✅ РАБОТАЕТ' if main_running else '❌ ОСТАНОВЛЕН'}")
print(f"   Forwarder: {'✅ РАБОТАЕТ' if forwarder_running else '❌ ОСТАНОВЛЕН'}")

if not main_running:
    print("\n   Ошибки main.py:")
    try:
        stdout, stderr = main_proc.communicate(timeout=2)
        if stderr:
            print(stderr[-500:])
        if stdout:
            print(stdout[-500:])
    except:
        pass

if not forwarder_running:
    print("\n   Ошибки forwarder.py:")
    try:
        stdout, stderr = forwarder_proc.communicate(timeout=2)
        if stderr:
            print(stderr[-500:])
        if stdout:
            print(stdout[-500:])
    except:
        pass

if main_running and forwarder_running:
    print("\n" + "=" * 60)
    print("✅✅✅ ОБА БОТА ЗАПУЩЕНЫ И РАБОТАЮТ!")
    print("=" * 60)
    print(f"\nMain bot PID: {main_proc.pid}")
    print(f"Forwarder PID: {forwarder_proc.pid}")
    print("\nБоты работают в фоне.")
    print("Проверьте bot.log и forwarder.log для подтверждения.")
    
    # Держим скрипт запущенным
    try:
        while True:
            if main_proc.poll() is not None:
                print("\n⚠️ Main bot остановился!")
                break
            if forwarder_proc.poll() is not None:
                print("\n⚠️ Forwarder остановился!")
                break
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nОстанавливаю ботов...")
        main_proc.terminate()
        forwarder_proc.terminate()
        time.sleep(2)
        main_proc.kill()
        forwarder_proc.kill()
        print("Готово.")
else:
    print("\n" + "=" * 60)
    print("❌ НЕ УДАЛОСЬ ЗАПУСТИТЬ БОТОВ")
    print("=" * 60)
    sys.exit(1)





#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Проверка и запуск ботов"""
import sys
import os
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("=" * 60)
print("ПРОВЕРКА И ЗАПУСК БОТОВ")
print("=" * 60)

# Проверка импортов
print("\n1. Проверка импортов...")
try:
    print("   Проверка main.py...")
    import main
    print("   ✅ main.py импортирован успешно")
except Exception as e:
    print(f"   ❌ Ошибка импорта main.py: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("   Проверка forwarder.py...")
    import forwarder
    print("   ✅ forwarder.py импортирован успешно")
except Exception as e:
    print(f"   ❌ Ошибка импорта forwarder.py: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Останавливаем старые процессы
print("\n2. Остановка старых процессов...")
try:
    if sys.platform == 'win32':
        subprocess.run(["taskkill", "/F", "/IM", "python.exe"], 
                      capture_output=True, check=False)
    time.sleep(2)
except:
    pass

# Запускаем ботов
print("\n3. Запуск ботов...")
print("   Запускаю main.py...")
main_proc = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

time.sleep(3)

print("   Запускаю forwarder.py...")
forwarder_proc = subprocess.Popen(
    [sys.executable, "forwarder.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

time.sleep(5)

# Проверяем статус
print("\n4. Проверка статуса...")
main_running = main_proc.poll() is None
forwarder_running = forwarder_proc.poll() is None

print(f"   Main bot: {'✅ РАБОТАЕТ' if main_running else '❌ ОСТАНОВЛЕН'}")
print(f"   Forwarder: {'✅ РАБОТАЕТ' if forwarder_running else '❌ ОСТАНОВЛЕН'}")

if not main_running:
    print("\n   Ошибки main.py:")
    try:
        stdout, stderr = main_proc.communicate(timeout=2)
        if stderr:
            print(stderr[-500:])
        if stdout:
            print(stdout[-500:])
    except:
        pass

if not forwarder_running:
    print("\n   Ошибки forwarder.py:")
    try:
        stdout, stderr = forwarder_proc.communicate(timeout=2)
        if stderr:
            print(stderr[-500:])
        if stdout:
            print(stdout[-500:])
    except:
        pass

if main_running and forwarder_running:
    print("\n" + "=" * 60)
    print("✅✅✅ ОБА БОТА ЗАПУЩЕНЫ И РАБОТАЮТ!")
    print("=" * 60)
    print(f"\nMain bot PID: {main_proc.pid}")
    print(f"Forwarder PID: {forwarder_proc.pid}")
    print("\nБоты работают в фоне.")
    print("Проверьте bot.log и forwarder.log для подтверждения.")
    
    # Держим скрипт запущенным
    try:
        while True:
            if main_proc.poll() is not None:
                print("\n⚠️ Main bot остановился!")
                break
            if forwarder_proc.poll() is not None:
                print("\n⚠️ Forwarder остановился!")
                break
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nОстанавливаю ботов...")
        main_proc.terminate()
        forwarder_proc.terminate()
        time.sleep(2)
        main_proc.kill()
        forwarder_proc.kill()
        print("Готово.")
else:
    print("\n" + "=" * 60)
    print("❌ НЕ УДАЛОСЬ ЗАПУСТИТЬ БОТОВ")
    print("=" * 60)
    sys.exit(1)






