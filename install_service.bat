@echo off
:: Run this script as Administrator to install NewsBot as a Windows service.
:: Right-click -> "Run as administrator"

set NSSM="C:\Users\spud9\AppData\Local\Microsoft\WinGet\Packages\NSSM.NSSM_Microsoft.Winget.Source_8wekyb3d8bbwe\nssm-2.24-101-g897c7ad\win64\nssm.exe"
set BOT_DIR="C:\Users\spud9\OneDrive\Documents\newsbot strikes back"
set PYTHON="C:\Python314\python.exe"

:: Remove existing service if present (handles re-installs cleanly)
%NSSM% status NewsBot >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Found existing NewsBot service, removing it first...
    %NSSM% stop NewsBot >nul 2>&1
    %NSSM% remove NewsBot confirm
)

echo Installing NewsBot service...
%NSSM% install NewsBot %PYTHON% "main.py"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install service. Are you running as Administrator?
    pause
    exit /b 1
)

echo Configuring service...

:: Set the working directory so .env and config.py are found
%NSSM% set NewsBot AppDirectory %BOT_DIR%

:: Redirect stdout/stderr to log files (rotated; do not grow forever)
%NSSM% set NewsBot AppStdout %BOT_DIR%\service_stdout.log
%NSSM% set NewsBot AppStderr %BOT_DIR%\service_stderr.log
%NSSM% set NewsBot AppStdoutCreationDisposition 4
%NSSM% set NewsBot AppStderrCreationDisposition 4
:: Rotate while running when a log exceeds ~10 MB
%NSSM% set NewsBot AppRotateFiles 1
%NSSM% set NewsBot AppRotateOnline 1
%NSSM% set NewsBot AppRotateBytes 10485760

:: Restart automatically on crash, with 5-second delay
%NSSM% set NewsBot AppRestartDelay 5000

:: Start automatically on boot
%NSSM% set NewsBot Start SERVICE_AUTO_START

:: Give a description
%NSSM% set NewsBot Description "Discord news aggregator bot - polls RSS/Telegram, categorizes, and posts to Discord"

echo.
echo Service installed successfully!
echo.
echo To start it now:   %NSSM% start NewsBot
echo To stop it:        %NSSM% stop NewsBot
echo To check status:   %NSSM% status NewsBot
echo To uninstall:      %NSSM% remove NewsBot confirm
echo.

:: Start the service now
set /p START_NOW="Start the service now? (Y/N): "
if /i "%START_NOW%"=="Y" (
    %NSSM% start NewsBot
    echo NewsBot service started!
) else (
    echo Service installed but not started. Start it manually when ready.
)

pause
