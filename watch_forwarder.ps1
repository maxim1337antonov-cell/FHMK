$ErrorActionPreference = "Continue"

$projectDir = "C:\p2p-bot"
$scriptName = "forwarder.py"
$forwarderLog = Join-Path $projectDir "forwarder.log"
$watchdogLog = Join-Path $projectDir "watchdog_forwarder.log"

# Настройки watchdog
$pollIntervalSec = 10
$staleThresholdSec = 120

function Write-WatchdogLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $watchdogLog -Value "[$timestamp] $Message"
}

function Get-ForwarderProcess {
    return Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -match "(^|\s)forwarder\.py(\s|$)"
    } | Select-Object -First 1
}

function Start-Forwarder {
    try {
        Write-WatchdogLog "Starting forwarder process..."
        $proc = Start-Process py -ArgumentList $scriptName -WorkingDirectory $projectDir -PassThru -WindowStyle Hidden
        Write-WatchdogLog "Forwarder started, PID=$($proc.Id)"
    } catch {
        Write-WatchdogLog "Failed to start forwarder: $($_.Exception.Message)"
    }
}

function Stop-Forwarder {
    param([int]$ProcessId)
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        Write-WatchdogLog "Forwarder stopped, PID=$ProcessId"
    } catch {
        Write-WatchdogLog "Failed to stop forwarder PID=$ProcessId: $($_.Exception.Message)"
    }
}

Write-WatchdogLog "Watchdog v2 started (stale_threshold=${staleThresholdSec}s)"

while ($true) {
    try {
        $forwarderProc = Get-ForwarderProcess

        if (-not $forwarderProc) {
            Write-WatchdogLog "Forwarder process not found"
            Start-Forwarder
            Start-Sleep -Seconds $pollIntervalSec
            continue
        }

        # Проверяем активность логов: если лог давно не обновлялся, считаем процесс зависшим
        if (Test-Path $forwarderLog) {
            $lastWrite = (Get-Item $forwarderLog).LastWriteTime
            $ageSec = [int]((Get-Date) - $lastWrite).TotalSeconds

            if ($ageSec -gt $staleThresholdSec) {
                Write-WatchdogLog "Forwarder log stale (${ageSec}s), restarting PID=$($forwarderProc.ProcessId)"
                Stop-Forwarder -ProcessId $forwarderProc.ProcessId
                Start-Sleep -Seconds 2
                Start-Forwarder
            }
        } else {
            Write-WatchdogLog "Forwarder log file not found: $forwarderLog"
        }
    } catch {
        Write-WatchdogLog "Watchdog loop error: $($_.Exception.Message)"
    }

    Start-Sleep -Seconds $pollIntervalSec
}
