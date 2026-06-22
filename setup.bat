@echo off
REM ============================================================
REM  LeadHunter - one-time setup
REM  Installs dependencies and prepares data folders.
REM ============================================================
setlocal
cd /d "%~dp0"

echo ============================================
echo   LeadHunter - Setup
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH. Install Python 3.10+ and retry.
    pause
    exit /b 1
)

echo [1/3] Upgrading pip...
python -m pip install --upgrade pip

echo.
echo [2/3] Installing dependencies...
python -m pip install -r requirements.txt

echo.
echo [3/3] Creating data folders...
if not exist data mkdir data
if not exist exports mkdir exports
if not exist reports mkdir reports
if not exist logs mkdir logs

echo.
echo ============================================
echo   Setup complete!
echo   - Run dashboard : run_dashboard.bat
echo   - Run a scan    : run_scan.bat
echo   - Run scheduler : run_scheduler.bat
echo ============================================
pause
endlocal
