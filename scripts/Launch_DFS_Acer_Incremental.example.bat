@echo off
setlocal
title DFS Optimizer - Update, Launch and Backup
set "DFS_DIR=%USERPROFILE%\Projects\DFS"
set "DFS_BACKUP_ROOT=D:\DFS-Backups"

cd /d "%DFS_DIR%" 2>nul
if errorlevel 1 (
    echo Could not open the DFS folder: %DFS_DIR%
    goto failed
)
if not exist ".git" (
    echo The DFS folder is not a Git checkout.
    goto failed
)
where git >nul 2>nul
if errorlevel 1 (
    echo Git was not found.
    goto failed
)
if not exist ".venv\Scripts\python.exe" (
    echo The DFS Python environment was not found.
    goto failed
)

echo Checking for local changes...
git status --porcelain >nul 2>nul
if errorlevel 1 goto failed
set "DFS_DIRTY="
for /f "delims=" %%L in ('git status --porcelain') do set "DFS_DIRTY=1"
if defined DFS_DIRTY (
    echo Update stopped to preserve these local changes:
    git status --short
    goto failed
)

echo.
echo Checking GitHub for updates...
git pull --ff-only
if errorlevel 1 goto failed

echo.
echo Checking required Python packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed

echo.
echo Starting DFS Optimizer...
echo Keep this window open. Backup runs after you close the app.
echo Use only one DFS window at a time so its history can be backed up safely.
".venv\Scripts\python.exe" app.py
set "DFS_APP_EXIT=%ERRORLEVEL%"

echo.
echo Backing up new or changed history content to USB...
".venv\Scripts\python.exe" history_backup.py --source "history" --destination "%DFS_BACKUP_ROOT%"
if errorlevel 1 goto backup_failed
if not "%DFS_APP_EXIT%"=="0" (
    echo The app exited with error code %DFS_APP_EXIT%. Its history was backed up.
    pause
    exit /b 1
)
echo.
echo Finished. New backup content verified; unchanged content reused.
echo Keep the entire incremental-v1 folder for restoration.
timeout /t 5 >nul
exit /b 0

:backup_failed
echo.
echo DFS has closed, but the USB backup was not completed.
echo Keep the original history files. Copy the error above for troubleshooting.
pause
exit /b 1

:failed
echo.
echo DFS update or launch did not complete.
echo Copy the error above for troubleshooting. Completed changes were not rolled back.
pause
exit /b 1
