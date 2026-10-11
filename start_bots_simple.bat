@echo off
cd /d C:\p2p-bot
echo Starting main bot...
start "P2P Bot" python main.py
timeout /t 3 /nobreak >nul
echo Starting forwarder...
start "Forwarder" python forwarder.py
timeout /t 5 /nobreak >nul
echo Checking processes...
tasklist | findstr python.exe
echo.
echo Check bot.log and forwarder.log for status
pause







