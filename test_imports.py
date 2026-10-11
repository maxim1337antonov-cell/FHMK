#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import traceback

print("Проверка импортов...")

try:
    print("1. config...")
    from config import BOT_TOKEN
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

try:
    print("2. database...")
    from database import init_db
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

try:
    print("3. handlers.deal_handlers...")
    from handlers.deal_handlers import router
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

try:
    print("4. main...")
    import main
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

try:
    print("5. forwarder...")
    import forwarder
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

print("\nПроверка завершена.")

import sys
import traceback

print("Проверка импортов...")

try:
    print("1. config...")
    from config import BOT_TOKEN
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

try:
    print("2. database...")
    from database import init_db
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

try:
    print("3. handlers.deal_handlers...")
    from handlers.deal_handlers import router
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

try:
    print("4. main...")
    import main
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

try:
    print("5. forwarder...")
    import forwarder
    print("   OK")
except Exception as e:
    print(f"   ERROR: {e}")
    traceback.print_exc()

print("\nПроверка завершена.")
