@echo off
cd /d C:\p2p-bot
echo Starting main bot...
start "Main Bot" cmd /k "cd /d C:\p2p-bot && py main.py"
timeout /t 3 /nobreak >nul
echo Starting forwarder...
start "Forwarder" cmd /k "cd /d C:\p2p-bot && py forwarder.py"
echo.
echo Bots started in separate windows.
echo Check the windows for status.
pause






