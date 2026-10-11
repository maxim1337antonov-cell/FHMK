# Bot start script
$ErrorActionPreference = "Continue"
$projectDir = "C:\p2p-bot"

Write-Host "Stopping bot processes..."
# Останавливаем Python: main.py и forwarder.py
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -match "(main|forwarder)\.py"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# Останавливаем watchdog (PowerShell с watch_forwarder)
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {
    $_.CommandLine -match "watch_forwarder"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3

# Прокси VPN для Python (Telegram в приложении работает, Python — нет без этого)
if (-not $env:TELEGRAM_PROXY) { $env:TELEGRAM_PROXY = "http://127.0.0.1:10801" }
$env:HTTPS_PROXY = $env:TELEGRAM_PROXY
$env:HTTP_PROXY = $env:TELEGRAM_PROXY
Write-Host "TELEGRAM_PROXY=$($env:TELEGRAM_PROXY)"

Write-Host "Starting main bot..."
Start-Process py -ArgumentList "main.py" -WorkingDirectory $projectDir -PassThru -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 3

Write-Host "Starting forwarder..."
Start-Process py -ArgumentList "forwarder.py" -WorkingDirectory $projectDir -PassThru -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 3

Write-Host "Starting forwarder watchdog..."
Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$projectDir\watch_forwarder.ps1`"" -WorkingDirectory $projectDir -PassThru -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 5

Write-Host "Checking processes..."
$processes = Get-Process python -ErrorAction SilentlyContinue
if ($processes) {
    Write-Host "Python processes: $($processes.Count)"
    $processes | Format-Table Id, ProcessName, StartTime
} else {
    Write-Host "No python processes found!"
}

Write-Host "`nChecking logs..."
Start-Sleep -Seconds 2
Write-Host "`nLast lines bot.log:"
Get-Content "$projectDir\bot.log" -Tail 5 -ErrorAction SilentlyContinue
Write-Host "`nLast lines forwarder.log:"
Get-Content "$projectDir\forwarder.log" -Tail 5 -ErrorAction SilentlyContinue
Write-Host "`nLast lines watchdog_forwarder.log:"
Get-Content "$projectDir\watchdog_forwarder.log" -Tail 5 -ErrorAction SilentlyContinue

Write-Host "`nDone!"
