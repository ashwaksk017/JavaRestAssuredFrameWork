@echo off
rem Start the AI-Integrated QA Workbench and open it in the browser.
rem Double-click this file, or run it from a console. Python 3.9+ is found,
rem or installed (winget) when there is none; tools\agent\launch.py then
rem installs what requirements.txt lists and this Python lacks, and starts
rem the server -- that is where the behaviour is described.
setlocal
cd /d "%~dp0"

call :find_python
if defined PY goto :start

echo Python 3.9 or newer was not found.
where winget >nul 2>nul
if errorlevel 1 goto :no_winget
echo Installing Python 3.12 for this user with winget (a few minutes) ...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
rem This console's PATH predates the install: look where winget puts it.
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
call :find_python
if defined PY goto :start
echo Python was installed, but this window cannot see it yet. Close it and run start-workbench.bat again.
pause
exit /b 1

:no_winget
echo Install it from https://www.python.org/downloads/
echo ^(tick "Add python.exe to PATH" in the installer^), then run this again.
pause
exit /b 1

:start
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
exit /b 0

rem A Python that RUNS and is 3.9+: "python" can be the Microsoft Store's
rem stand-in, which is on PATH but is not Python. No ( ) blocks here: the
rem ")" in "(3, 9)" would end one early.
:find_python
set "PY="
where py >nul 2>nul || goto :try_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3"
if defined PY exit /b 0
:try_python
where python >nul 2>nul || exit /b 0
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python"
exit /b 0
