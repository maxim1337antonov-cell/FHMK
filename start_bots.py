#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Простой скрипт для запуска обоих ботов"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Запускаю ботов...")

# Запускаем основной бот
print("1. Запускаю main.py...")
main_proc = subprocess.Popen([sys.executable, "main.py"], cwd=str(BASE_DIR))

time.sleep(3)

# Запускаем forwarder
print("2. Запускаю forwarder.py...")
forwarder_proc = subprocess.Popen([sys.executable, "forwarder.py"], cwd=str(BASE_DIR))

time.sleep(5)

print("\n✅ Боты запущены!")
print(f"Main bot PID: {main_proc.pid}")
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





#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Простой скрипт для запуска обоих ботов"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Запускаю ботов...")

# Запускаем основной бот
print("1. Запускаю main.py...")
main_proc = subprocess.Popen([sys.executable, "main.py"], cwd=str(BASE_DIR))

time.sleep(3)

# Запускаем forwarder
print("2. Запускаю forwarder.py...")
forwarder_proc = subprocess.Popen([sys.executable, "forwarder.py"], cwd=str(BASE_DIR))

time.sleep(5)

print("\n✅ Боты запущены!")
print(f"Main bot PID: {main_proc.pid}")
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






