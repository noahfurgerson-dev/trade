@echo off
REM  ══════════════════════════════════════════════════════════════════════
REM  Trading Scheduler — Windows Task Scheduler installer
REM  ══════════════════════════════════════════════════════════════════════
REM  Creates a repeating task that runs every 30 minutes AND wakes the
REM  computer from sleep to do so.  Run ONCE as Administrator.
REM
REM  After running this:
REM    • The scheduler fires every 30 min, 24/7
REM    • Sleep/hibernate does NOT stop it — Windows wakes the PC to run it
REM    • No Python process stays open between cycles (no freeze risk)
REM    • Logs go to:  data\scheduler.log
REM  ══════════════════════════════════════════════════════════════════════

setlocal EnableDelayedExpansion

set TASK_NAME=TradingSchedulerV2
set SCRIPT=%~dp0run_cycle_once.py
set PYTHON=python
set INTERVAL_MINS=30

echo.
echo  Installing Trading Scheduler task: %TASK_NAME%
echo  Script : %SCRIPT%
echo  Repeats: every %INTERVAL_MINS% minutes
echo  Wake   : YES (computer will wake from sleep to trade)
echo.

REM ── Remove old task if it exists ─────────────────────────────────────────────
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /delete /tn "TradingScheduler" /f >nul 2>&1

REM ── Create task with minute-repeat trigger ────────────────────────────────────
REM    /sc MINUTE /mo 30  = repeat every 30 minutes
REM    /du 9999:59        = run indefinitely (duration 9999 hrs 59 min)
REM    /rl HIGHEST        = run with highest privileges
REM    /f                 = force-create (overwrite if exists)

schtasks /create /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\"" ^
  /sc MINUTE /mo %INTERVAL_MINS% ^
  /du 9999:59 ^
  /ru "%USERNAME%" ^
  /rl HIGHEST ^
  /f

if not %ERRORLEVEL%==0 (
    echo.
    echo  ERROR: Task creation failed. Try running as Administrator.
    pause
    exit /b 1
)

echo.
echo  Task created. Now enabling Wake-to-Run via PowerShell...
echo.

REM ── Enable WakeToRun via PowerShell ─────────────────────────────────────────
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$t = Get-ScheduledTask -TaskName '%TASK_NAME%'; $s = $t.Settings; $s.WakeToRun = $true; Set-ScheduledTask -TaskName '%TASK_NAME%' -Settings $s"

if %ERRORLEVEL%==0 (
    echo  Wake-to-Run ENABLED.
) else (
    echo  WARNING: Could not enable Wake-to-Run. The task will still run,
    echo  but may not wake the computer from sleep. Check Power Settings.
)

echo.
echo  ══════════════════════════════════════════════════════════════════
echo   SUCCESS
echo   - Scheduler runs every %INTERVAL_MINS% minutes automatically
echo   - Computer will WAKE FROM SLEEP to execute trades
echo   - Logs: %~dp0data\scheduler.log
echo.
echo   To remove:   schtasks /delete /tn %TASK_NAME% /f
echo   To run now:  schtasks /run /tn %TASK_NAME%
echo   To view log: notepad %~dp0data\scheduler.log
echo  ══════════════════════════════════════════════════════════════════
echo.
pause
