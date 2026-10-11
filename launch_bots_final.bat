@echo off
cd /d C:\p2p-bot
start /B py main.py
timeout /t 3 /nobreak >nul
start /B py forwarder.py
timeout /t 5 /nobreak >nul
tasklist | findstr python.exe
echo Bots started in background






