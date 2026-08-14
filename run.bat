@echo off
setlocal
cd /d "%~dp0"

REM ---------------------------------------------------------------
REM  Ortho photo automation - Windows launcher
REM  ASCII only, CRLF line endings.  cmd.exe reads .bat in the system
REM  code page (CP949 on Korean Windows); UTF-8 Korean here would be
REM  parsed as commands.  User-facing Korean text is printed by Python.
REM ---------------------------------------------------------------

REM -- Run from a copy.  cmd.exe reads a .bat by BYTE OFFSET and re-opens the
REM -- file after every command, so an update that rewrites this file while the
REM -- app is running leaves cmd reading the new file at the old offset -- in
REM -- the middle of a line.  That is how a PC with Python 3.12 was told its
REM -- Python was "older than 3.10": the fragment failed and the next line was
REM -- "if errorlevel 1 goto oldpython".  The copy in TEMP is never rewritten.
REM --
REM -- The whole branch is ONE parenthesised block on purpose: cmd parses a
REM -- block into memory before running it, so the "exit /b" below is already
REM -- buffered and is not re-read from the rewritten file when the copy ends.
if not defined CROCS_HOME (
  set "CROCS_HOME=%~dp0."
  copy /y "%~f0" "%TEMP%\crocs_run.bat" >nul 2>&1
  if exist "%TEMP%\crocs_run.bat" (
    call "%TEMP%\crocs_run.bat"
    exit /b
  )
)
if defined CROCS_HOME cd /d "%CROCS_HOME%"

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
echo         Found:
%PYEXE% --version
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
