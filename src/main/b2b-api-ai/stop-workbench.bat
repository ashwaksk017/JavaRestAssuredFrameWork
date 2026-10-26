@echo off
rem Stop the AI-Integrated QA Workbench started from THIS folder.
rem It ends only a server that says it is this folder's workbench; anything
rem else on the port is left alone and named. See tools\agent\launch.py.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3 was not found, so nothing could be asked or stopped.
    pause
    exit /b 1
)

%PY% -B tools\agent\launch.py stop %*
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)
timeout /t 4 >nul 2>nul
endlocal
