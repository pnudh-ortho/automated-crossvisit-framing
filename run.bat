@echo off
setlocal
cd /d "%~dp0"

REM ---------------------------------------------------------------
REM  Ortho photo automation - Windows launcher
REM  ASCII only, CRLF line endings.  cmd.exe reads .bat in the system
REM  code page (CP949 on Korean Windows); UTF-8 Korean here would be
REM  parsed as commands.  User-facing Korean text is printed by Python.
REM ---------------------------------------------------------------

REM -- The app uses 3.10 syntax; a venv built with an older Python dies at
REM -- import time.  Rebuild it rather than failing every launch.
if not exist ".venv\Scripts\python.exe" goto findpy
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 goto run
echo [setup] The virtual environment uses an old Python - rebuilding...
rmdir /s /q ".venv"
:findpy

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
%PYEXE% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto oldpython
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

:oldpython
echo.
echo [error] The Python found on this PC is older than 3.10.
echo         Install a newer one, then run this again:
echo           https://www.python.org/downloads/
echo.
pause
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
