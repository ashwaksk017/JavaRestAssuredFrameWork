@echo off
rem Start the AI Enabled Test WorkBench and open it in the browser.
rem Double-click this file, or run it from a console. It does nothing but
rem call tools\agent\launch.py, which is where the behaviour is described.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" in the installer^), then run this again.
    pause
    exit /b 1
)

%PY% -B tools\agent\launch.py start %*
if errorlevel 1 (
    echo.
    echo The workbench was NOT started. The reason is above.
    pause
    exit /b 1
)
rem Long enough to read the address when this was double-clicked.
timeout /t 4 >nul 2>nul
endlocal
