@echo off
REM Nightly tuning agent runner — invoked by Windows Task Scheduler.
REM Activates the project venv if present, cd's to the project root, runs the
REM tuning agent. Output appended to bot.log alongside the main bot's logs.

setlocal

set "PROJECT_ROOT=%~dp0..\.."
pushd "%PROJECT_ROOT%"

if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

echo [%date% %time%] starting nightly tuning agent >> bot.log
python evals\tuning_agent.py >> bot.log 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] tuning agent exited with code %RC% >> bot.log

popd
endlocal & exit /b %RC%
