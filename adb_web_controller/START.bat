@echo off
title ADB Web Controller - High Performance
color 0B

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║       ADB Web Controller  v2.0           ║
echo  ║   scrcpy protocol  ·  H.264 streaming    ║
echo  ╚══════════════════════════════════════════╝
echo.

:: ── Check Python ──────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Download: https://www.python.org/downloads/
    echo  Make sure to check "Add to PATH" during install.
    pause & exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo  Python: %%i

:: ── Check / install dependencies ─────────────────────────────────────────────
echo.
echo  Checking dependencies...
python -c "import flask, flask_sock" >nul 2>&1
if errorlevel 1 (
    echo  Installing Flask...
    pip install flask flask-sock --quiet
)
echo  Dependencies OK

:: ── Check ADB ────────────────────────────────────────────────────────────────
echo.
adb version >nul 2>&1
if errorlevel 1 (
    echo  [WARNING] ADB not found in PATH.
    echo  Download Platform Tools: https://developer.android.com/tools/releases/platform-tools
    echo  Extract and add the folder to your PATH, then re-run this script.
    echo.
    echo  If you have ADB elsewhere, edit server.py and set:
    echo    ADB = "C:\\path\\to\\adb.exe"
    echo.
    pause
)

:: ── Check device ─────────────────────────────────────────────────────────────
echo.
echo  Checking for connected device...
for /f "skip=1 tokens=1,2" %%a in ('adb devices 2^>nul') do (
    if "%%b"=="device" (
        echo  Device found: %%a
    )
)

:: ── Launch ────────────────────────────────────────────────────────────────────
echo.
echo  Starting server...
echo  Open: http://localhost:7070
echo.

:: Open browser after short delay
::start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:7070"

:: Run server
python server.py

echo.
echo  Server stopped.
pause
