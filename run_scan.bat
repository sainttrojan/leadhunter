@echo off
REM ============================================================
REM  LeadHunter - Run a lead-generation scan
REM  Customize QUERY / CITY / COUNTRY below, or pass CLI args.
REM  Example: run_scan.bat --query "Dental Clinics" --city Asyut
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Default search (used if no args are supplied)
set "DEFAULT_QUERY=Dental Clinics"
set "DEFAULT_CITY=Asyut"
set "DEFAULT_COUNTRY=Egypt"

set "ARGS=%*"
if "%ARGS%"=="" (
    echo Running default scan: !DEFAULT_QUERY! in !DEFAULT_CITY!, !DEFAULT_COUNTRY!...
    python -m leadhunter scan --query "!DEFAULT_QUERY!" --city "!DEFAULT_CITY!" --country "!DEFAULT_COUNTRY!" --export csv
) else (
    python -m leadhunter scan %ARGS%
)

echo.
pause
endlocal
