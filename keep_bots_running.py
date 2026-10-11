#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Скрипт для поддержания работы ботов"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

def start_bots():
    """Запускает оба бота"""
    print("Запускаю основной бот...")
    main_bot = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(BASE_DIR)
    )
    
    time.sleep(3)
    
    print("Запускаю forwarder...")
    forwarder = subprocess.Popen(
        [sys.executable, "forwarder.py"],
        cwd=str(BASE_DIR)
    )
    
    return main_bot, forwarder

if __name__ == "__main__":
    main_bot, forwarder = start_bots()
    print(f"\n✅ Боты запущены!")
    print(f"Main bot PID: {main_bot.pid}")
    print(f"Forwarder PID: {forwarder.pid}")
    print("\nБоты работают. Нажмите Ctrl+C для остановки...")
    
    try:
        while True:
            if main_bot.poll() is not None:
                print("\n⚠️ Main bot остановился, перезапускаю...")
                main_bot = subprocess.Popen([sys.executable, "main.py"], cwd=str(BASE_DIR))
            if forwarder.poll() is not None:
                print("\n⚠️ Forwarder остановился, перезапускаю...")
                forwarder = subprocess.Popen([sys.executable, "forwarder.py"], cwd=str(BASE_DIR))
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nОстанавливаю ботов...")
        main_bot.terminate()
        forwarder.terminate()
        time.sleep(2)
        main_bot.kill()
        forwarder.kill()
        print("Готово.")





#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Скрипт для поддержания работы ботов"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

def start_bots():
    """Запускает оба бота"""
    print("Запускаю основной бот...")
    main_bot = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(BASE_DIR)
    )
    
    time.sleep(3)
    
    print("Запускаю forwarder...")
    forwarder = subprocess.Popen(
        [sys.executable, "forwarder.py"],
        cwd=str(BASE_DIR)
    )
    
    return main_bot, forwarder

if __name__ == "__main__":
    main_bot, forwarder = start_bots()
    print(f"\n✅ Боты запущены!")
    print(f"Main bot PID: {main_bot.pid}")
    print(f"Forwarder PID: {forwarder.pid}")
    print("\nБоты работают. Нажмите Ctrl+C для остановки...")
    
    try:
        while True:
            if main_bot.poll() is not None:
                print("\n⚠️ Main bot остановился, перезапускаю...")
                main_bot = subprocess.Popen([sys.executable, "main.py"], cwd=str(BASE_DIR))
            if forwarder.poll() is not None:
                print("\n⚠️ Forwarder остановился, перезапускаю...")
                forwarder = subprocess.Popen([sys.executable, "forwarder.py"], cwd=str(BASE_DIR))
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nОстанавливаю ботов...")
        main_bot.terminate()
        forwarder.terminate()
        time.sleep(2)
        main_bot.kill()
        forwarder.kill()
        print("Готово.")






