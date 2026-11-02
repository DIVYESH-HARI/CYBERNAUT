@echo off
setlocal
cd /d "%~dp0"
title ScholarTrack

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.11+ from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn, httpx, bs4, lxml, tzdata" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages ^(first run only^)...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Package install failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo.
echo Starting ScholarTrack... the browser will open automatically.
echo Keep this window open while using the app. Press Ctrl+C to stop.
echo.
python run.py
pause
