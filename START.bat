@echo off
REM ═══════════════════════════════════════════════════════════════════════════
REM  Phone Mirror — Server Launcher (START.bat)
REM ═══════════════════════════════════════════════════════════════════════════
REM  Double-click this file to start the Phone Mirror server.
REM  It checks for Python, ADB, Flask, and scrcpy-server before launching.
REM
REM  Goes in: Same folder as server.py (the adb_web_controller folder)
REM ═══════════════════════════════════════════════════════════════════════════

REM -- Set window title for easy identification --
title Phone Mirror Server
REM -- Set cyan text on black background --
color 0B

echo.
echo  ========================================
echo    Phone Mirror Server
echo    scrcpy protocol - H.264 + AAC
echo  ========================================
echo.

REM -- Change to the folder where this .bat file lives --
REM    (so server.py and scrcpy-server.jar are found)
cd /d "%~dp0"

REM ── Step 1: Check Python is installed ──────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Download: https://www.python.org/downloads/
    echo  Make sure to check "Add to PATH" during install.
    pause & exit /b 1
)
REM -- Show which Python version is being used --
for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo  Python: %%i

REM ── Step 2: Check and install Flask dependencies ───────────────────────
echo.
echo  Checking dependencies...
python -c "import flask, flask_sock" >nul 2>&1
if errorlevel 1 (
    echo  Installing Flask and flask-sock...
    pip install flask flask-sock --quiet
)
echo  Dependencies OK

REM ── Step 3: Check ADB is available ─────────────────────────────────────
echo.
adb version >nul 2>&1
if errorlevel 1 (
    echo  [WARNING] ADB not found in PATH.
    echo  Download Platform Tools:
    echo    https://developer.android.com/tools/releases/platform-tools
    echo  Extract and add the folder to your system PATH.
    echo.
    pause
)

REM ── Step 4: Check for connected device ─────────────────────────────────
echo.
echo  Checking for connected device...
for /f "skip=1 tokens=1,2" %%a in ('adb devices 2^>nul') do (
    if "%%b"=="device" (
        echo  Device found: %%a
    )
)

REM ── Step 5: Launch the server ──────────────────────────────────────────
echo.
echo  Starting server...
echo  Browser URL: http://localhost:7070
echo  (Server auto-connects when a phone is plugged in)
echo.

REM -- Run the Python server (blocks until Ctrl+C) --
python server.py

REM -- Server has stopped --
echo.
echo  Server stopped.
pause
