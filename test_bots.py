#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Тест запуска ботов"""
import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Тестирую импорты...")

try:
    print("1. Импорт main...")
    import main
    print("   ✅ main импортирован")
except Exception as e:
    print(f"   ❌ Ошибка импорта main: {e}")
    import traceback
    traceback.print_exc()

try:
    print("2. Импорт forwarder...")
    import forwarder
    print("   ✅ forwarder импортирован")
except Exception as e:
    print(f"   ❌ Ошибка импорта forwarder: {e}")
    import traceback
    traceback.print_exc()

print("\nИмпорты проверены.")





#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Тест запуска ботов"""
import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Тестирую импорты...")

try:
    print("1. Импорт main...")
    import main
    print("   ✅ main импортирован")
except Exception as e:
    print(f"   ❌ Ошибка импорта main: {e}")
    import traceback
    traceback.print_exc()

try:
    print("2. Импорт forwarder...")
    import forwarder
    print("   ✅ forwarder импортирован")
except Exception as e:
    print(f"   ❌ Ошибка импорта forwarder: {e}")
    import traceback
    traceback.print_exc()

print("\nИмпорты проверены.")






