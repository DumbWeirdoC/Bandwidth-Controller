@echo off
title Bandwidth Controller Launcher
cd /d "%~dp0"

:: Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb runAs"
    exit /b
)

echo ===================================================
echo     Bandwidth Controller - Smart Traffic Limiter   
echo ===================================================
echo.
echo [*] Checking Python environment...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from python.org or Microsoft Store.
    pause
    exit /b
)

echo [*] Checking dependencies...
python -m pip install -r requirements.txt --quiet

echo [*] Starting Bandwidth Controller Web Application...
python main.py

pause
