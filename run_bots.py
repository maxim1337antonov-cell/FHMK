#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Скрипт для запуска ботов в фоне"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Stopping old Python processes...")
try:
    subprocess.run(["taskkill", "/F", "/IM", "python.exe"], 
                   capture_output=True, check=False)
except:
    pass

time.sleep(2)

print("Starting main bot...")
main_bot = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

time.sleep(3)

print("Starting forwarder...")
forwarder = subprocess.Popen(
    [sys.executable, "forwarder.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

time.sleep(5)

print("Checking processes...")
# Check processes using subprocess
try:
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"], 
                          capture_output=True, text=True)
    if "python.exe" in result.stdout:
        print("Python processes found!")
        print(result.stdout)
    else:
        print("No Python processes found!")
except:
    print("Could not check processes")

print("\nBots should be running. Check bot.log and forwarder.log for status.")


"""Скрипт для запуска ботов в фоне"""
import subprocess
import sys
import time
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)

print("Stopping old Python processes...")
try:
    subprocess.run(["taskkill", "/F", "/IM", "python.exe"], 
                   capture_output=True, check=False)
except:
    pass

time.sleep(2)

print("Starting main bot...")
main_bot = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

time.sleep(3)

print("Starting forwarder...")
forwarder = subprocess.Popen(
    [sys.executable, "forwarder.py"],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

time.sleep(5)

print("Checking processes...")
# Check processes using subprocess
try:
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"], 
                          capture_output=True, text=True)
    if "python.exe" in result.stdout:
        print("Python processes found!")
        print(result.stdout)
    else:
        print("No Python processes found!")
except:
    print("Could not check processes")

print("\nBots should be running. Check bot.log and forwarder.log for status.")

