@echo off
REM  Registers the trading scheduler to start automatically at Windows login.
REM  Run this ONCE as Administrator.

set TASK_NAME=TradingScheduler
set SCRIPT=%~dp0run_scheduler.py
set PYTHON=python

echo Installing auto-start task: %TASK_NAME%

schtasks /create /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\" --interval 30" ^
  /sc ONLOGON ^
  /ru "%USERNAME%" ^
  /rl HIGHEST ^
  /f

if %ERRORLEVEL%==0 (
    echo.
    echo SUCCESS — scheduler will start automatically at every login.
    echo To remove:  schtasks /delete /tn %TASK_NAME% /f
    echo To run now: schtasks /run /tn %TASK_NAME%
) else (
    echo.
    echo FAILED — try running this script as Administrator.
)
pause
