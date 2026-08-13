@echo off
setlocal
cd /d "%~dp0"

REM ---------------------------------------------------------------
REM  Ortho photo automation - Windows launcher
REM  ASCII only, CRLF line endings.  cmd.exe reads .bat in the system
REM  code page (CP949 on Korean Windows); UTF-8 Korean here would be
REM  parsed as commands.  User-facing Korean text is printed by Python.
REM ---------------------------------------------------------------

if exist ".venv\Scripts\python.exe" goto run

REM -- find a real Python.  The one under WindowsApps is a 2-byte Store
REM -- stub that just opens the Microsoft Store, so prefer the py launcher.
set PYEXE=
py -3 --version >nul 2>&1
if not errorlevel 1 set PYEXE=py -3
if defined PYEXE goto make
python --version >nul 2>&1
if errorlevel 1 goto nopython
for /f "delims=" %%p in ('where python 2^>nul') do (
  if not defined PYEXE set PYEXE=%%p
)
if not defined PYEXE goto nopython

:make
echo [setup] Creating virtual environment...
%PYEXE% -m venv .venv
if errorlevel 1 goto nopython
call ".venv\Scripts\activate.bat"
echo [setup] Installing dependencies (a few minutes on first run)...
python -m pip install --upgrade pip
pip install -r webapp\requirements.txt
if errorlevel 1 goto nodeps
goto started

:run
call ".venv\Scripts\activate.bat"

:started
:loop
python webapp\backend\main.py
if %ERRORLEVEL% EQU 42 goto loop
goto done

:nopython
echo.
echo [error] Python 3.10 or newer is required.
echo         Download from https://www.python.org/downloads/
echo         Tick "Add python.exe to PATH" during installation.
echo.
echo         Note: the "python" under Microsoft Store is a stub and will not work.
echo.
pause
goto done

:nodeps
echo.
echo [error] Failed to install dependencies. Check your internet connection.
echo.
pause

:done
endlocal
