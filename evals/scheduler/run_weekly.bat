@echo off
REM Weekly report runner — invoked by Windows Task Scheduler on Sundays.

setlocal

set "PROJECT_ROOT=%~dp0..\.."
pushd "%PROJECT_ROOT%"

if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

echo [%date% %time%] generating weekly eval report >> bot.log
python evals\weekly_report.py >> bot.log 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] weekly report exited with code %RC% >> bot.log

popd
endlocal & exit /b %RC%
