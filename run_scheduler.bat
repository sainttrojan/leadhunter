@echo off
REM ============================================================
REM  LeadHunter - Run the background scheduler
REM  Runs daily/weekly/monthly scans for all saved searches.
REM  Recommended for a Windows VPS: run this in a persistent
REM  console, or wrap with NSSM / Task Scheduler.
REM ============================================================
setlocal
cd /d "%~dp0"

echo Starting LeadHunter scheduler (daily + weekly + monthly)...
echo Press Ctrl+C to stop.
echo.

python -m leadhunter schedule --preset daily weekly monthly %*

endlocal
