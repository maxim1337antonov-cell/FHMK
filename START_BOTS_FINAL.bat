@echo off
cd /d C:\p2p-bot
echo ========================================
echo ЗАПУСК БОТОВ
echo ========================================
echo.
echo Запускаю основной бот...
start "P2P Main Bot" cmd /k "cd /d C:\p2p-bot && python main.py"
timeout /t 5 /nobreak >nul
echo Запускаю forwarder...
start "Forwarder Bot" cmd /k "cd /d C:\p2p-bot && python forwarder.py"
timeout /t 5 /nobreak >nul
echo.
echo ========================================
echo БОТЫ ЗАПУЩЕНЫ
echo ========================================
echo.
echo Проверьте открывшиеся окна:
echo - "P2P Main Bot" - основной бот
echo - "Forwarder Bot" - forwarder
echo.
echo Если боты работают, в окнах будут логи.
echo Если есть ошибки, они будут видны в окнах.
echo.
pause





@echo off
cd /d C:\p2p-bot
echo ========================================
echo ЗАПУСК БОТОВ
echo ========================================
echo.
echo Запускаю основной бот...
start "P2P Main Bot" cmd /k "cd /d C:\p2p-bot && python main.py"
timeout /t 5 /nobreak >nul
echo Запускаю forwarder...
start "Forwarder Bot" cmd /k "cd /d C:\p2p-bot && python forwarder.py"
timeout /t 5 /nobreak >nul
echo.
echo ========================================
echo БОТЫ ЗАПУЩЕНЫ
echo ========================================
echo.
echo Проверьте открывшиеся окна:
echo - "P2P Main Bot" - основной бот
echo - "Forwarder Bot" - forwarder
echo.
echo Если боты работают, в окнах будут логи.
echo Если есть ошибки, они будут видны в окнах.
echo.
pause






