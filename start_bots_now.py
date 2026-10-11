#!/usr/bin/env python
# -*- coding: utf-8 -*-
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Запускаю основной бот...")
p1 = subprocess.Popen([sys.executable, "main.py"], cwd=str(BASE_DIR))
time.sleep(3)
print("Запускаю forwarder...")
p2 = subprocess.Popen([sys.executable, "forwarder.py"], cwd=str(BASE_DIR))
time.sleep(5)
print(f"Main bot PID: {p1.pid}")
print(f"Forwarder PID: {p2.pid}")
print("Боты запущены. Проверьте процессы.")





#!/usr/bin/env python
# -*- coding: utf-8 -*-
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Запускаю основной бот...")
p1 = subprocess.Popen([sys.executable, "main.py"], cwd=str(BASE_DIR))
time.sleep(3)
print("Запускаю forwarder...")
p2 = subprocess.Popen([sys.executable, "forwarder.py"], cwd=str(BASE_DIR))
time.sleep(5)
print(f"Main bot PID: {p1.pid}")
print(f"Forwarder PID: {p2.pid}")
print("Боты запущены. Проверьте процессы.")






