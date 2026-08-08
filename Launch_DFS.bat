@echo off
cd /d "%~dp0"

echo Starting DFS app...
echo Folder: %CD%
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py app.py
    echo.
    pause
    exit /b
)

where python >nul 2>nul
if %errorlevel%==0 (
    python app.py
    echo.
    pause
    exit /b
)

echo Python was not found.
echo Install Python from python.org and check "Add python.exe to PATH".
echo.
pause
