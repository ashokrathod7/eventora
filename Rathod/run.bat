@echo off
setlocal
cd /d "%~dp0"
title Eventora - Python Server

where py >nul 2>&1
if %errorlevel%==0 (set "PY=py") else (set "PY=python")

if not exist "venv\Scripts\python.exe" (
    echo Creating Eventora virtual environment...
    %PY% -m venv venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

REM Only install packages when they are actually missing.
REM This lets Eventora start without internet after the first successful setup.
venv\Scripts\python.exe -c "import flask, werkzeug" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages...
    venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Package installation failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo.
echo ==========================================
echo       EVENTORA SERVER STARTED
echo ==========================================
echo Open: http://127.0.0.1:5000
echo Keep this window open.
echo ==========================================
venv\Scripts\python.exe app.py
pause
