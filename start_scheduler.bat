@echo off
title Trade Scheduler — 24/7 Autonomous Trading
cd /d "%~dp0"
echo Starting autonomous trading scheduler...
echo Trades will run every 30 minutes.
echo Close this window to stop trading.
echo.
python run_scheduler.py --interval 30
pause
