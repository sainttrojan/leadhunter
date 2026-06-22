@echo off
REM ============================================================
REM  LeadHunter - Launch the Streamlit dashboard
REM ============================================================
setlocal
cd /d "%~dp0"

echo Starting LeadHunter dashboard...
echo Open http://localhost:8501 in your browser.
echo Press Ctrl+C in this window to stop.
echo.

python -m leadhunter dashboard %*

endlocal
