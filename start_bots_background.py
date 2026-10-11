#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Запуск ботов в фоне"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Запускаю ботов в фоне...")

# Запускаем основной бот
print("Запускаю main.py...")
main_proc = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

time.sleep(3)

# Запускаем forwarder
print("Запускаю forwarder.py...")
forwarder_proc = subprocess.Popen(
    [sys.executable, "forwarder.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

time.sleep(5)

print(f"\n✅ Боты запущены!")
print(f"Main bot PID: {main_proc.pid}")
print(f"Forwarder PID: {forwarder_proc.pid}")
print("\nБоты работают в фоне.")
print("Проверьте bot.log и forwarder.log для подтверждения.")

# Проверяем статус
main_running = main_proc.poll() is None
forwarder_running = forwarder_proc.poll() is None

if main_running and forwarder_running:
    print("\n✅✅✅ ОБА БОТА РАБОТАЮТ!")
else:
    print(f"\n⚠️ Main bot: {'работает' if main_running else 'остановился'}")
    print(f"⚠️ Forwarder: {'работает' if forwarder_running else 'остановился'}")





#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Запуск ботов в фоне"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Запускаю ботов в фоне...")

# Запускаем основной бот
print("Запускаю main.py...")
main_proc = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

time.sleep(3)

# Запускаем forwarder
print("Запускаю forwarder.py...")
forwarder_proc = subprocess.Popen(
    [sys.executable, "forwarder.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

time.sleep(5)

print(f"\n✅ Боты запущены!")
print(f"Main bot PID: {main_proc.pid}")
print(f"Forwarder PID: {forwarder_proc.pid}")
print("\nБоты работают в фоне.")
print("Проверьте bot.log и forwarder.log для подтверждения.")

# Проверяем статус
main_running = main_proc.poll() is None
forwarder_running = forwarder_proc.poll() is None

if main_running and forwarder_running:
    print("\n✅✅✅ ОБА БОТА РАБОТАЮТ!")
else:
    print(f"\n⚠️ Main bot: {'работает' if main_running else 'остановился'}")
    print(f"⚠️ Forwarder: {'работает' if forwarder_running else 'остановился'}")






