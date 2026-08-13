@echo off
setlocal enabledelayedexpansion

REM ---------------------------------------------------------------
REM  Uninstall.  Works even when the app will not start -- that is
REM  often exactly when you want to remove it.
REM
REM  A .bat cannot delete the folder it is running from (cmd holds a
REM  handle), so this copies itself to %TEMP% and re-runs from there.
REM  ASCII only, CRLF endings: cmd reads .bat in the system code page.
REM ---------------------------------------------------------------

if "%~1"=="--stage2" goto stage2

cd /d "%~dp0"
set "PROG=%CD%"

REM -- find the patient data folder (settings.json wins, else sibling)
set "DATA=%PROG%_data"
if exist "settings.json" (
  for /f "tokens=2 delims=:" %%a in ('findstr /i "\"root\"" settings.json') do (
    set "RAW=%%a"
  )
  if defined RAW (
    set "RAW=!RAW:"=!"
    set "RAW=!RAW:,=!"
    for /f "tokens=* delims= " %%b in ("!RAW!") do set "DATA=%%b"
  )
)

echo ===============================================
echo   Uninstall
echo ===============================================
echo.
echo   Program folder : %PROG%
echo   Patient data   : !DATA!
echo.
echo   The program folder will be removed.
echo   Patient data is KEPT unless you ask otherwise.
echo.

set /p GO="Remove the program? (y/N): "
if /i not "%GO%"=="y" goto abort

set "DROP="
echo.
set /p D2="Also delete PATIENT DATA?  This cannot be undone. (y/N): "
if /i "%D2%"=="y" (
  echo.
  echo   Type  DELETE  to confirm removal of patient records.
  set /p W="  > "
  if /i "!W!"=="DELETE" (
    set "DROP=!DATA!"
  ) else (
    echo   Not confirmed - patient data will be kept.
  )
)

REM -- Git / Python.  These are system tools: ask one at a time.
REM    ".installed_tools" lists what OUR installer added; when it exists we
REM    say so, but we ask either way -- installs made before that file
REM    existed would otherwise have no way to remove them.
set "TOOLS="
set "MINE="
if exist ".installed_tools" set /p MINE=<.installed_tools

git --version >nul 2>&1
if not errorlevel 1 (
  echo.
  findstr /c:"Git.Git" ".installed_tools" >nul 2>&1
  if not errorlevel 1 (
    echo   Git was installed by this program.
  ) else (
    echo   Git may have been on this PC already.
  )
  set /p G1="  Remove Git? (y/N): "
  if /i "!G1!"=="y" set "TOOLS=!TOOLS! Git.Git"
)

set "PYFOUND="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYFOUND=1"
if not defined PYFOUND (
  python --version >nul 2>&1
  if not errorlevel 1 set "PYFOUND=1"
)
if defined PYFOUND (
  echo.
  findstr /c:"Python.Python" ".installed_tools" >nul 2>&1
  if not errorlevel 1 (
    echo   Python was installed by this program.
  ) else (
    echo   Python may have been on this PC already.
  )
  set /p P1="  Remove Python 3.12? (y/N): "
  if /i "!P1!"=="y" set "TOOLS=!TOOLS! Python.Python.3.12"
)

copy /y "%~f0" "%TEMP%\acf_uninstall.bat" >nul
start "" cmd /c ""%TEMP%\acf_uninstall.bat" --stage2 "%PROG%" "!DROP!" "!TOOLS!""
exit /b

:stage2
REM -- running from %TEMP% now; the program folder is free to delete
timeout /t 2 /nobreak >nul
set "PROG=%~2"
set "DROP=%~3"
set "TOOLS=%~4"

REM -- desktop shortcut (both the plain and the OneDrive desktop)
del /f /q "%USERPROFILE%\Desktop\CRoCs.lnk" 2>nul
del /f /q "%USERPROFILE%\OneDrive\Desktop\CRoCs.lnk" 2>nul

for %%T in (%TOOLS%) do (
  echo Removing %%T ...
  winget uninstall -e --id %%T --silent --accept-source-agreements
)

echo Removing %PROG% ...
rmdir /s /q "%PROG%" 2>nul
if exist "%PROG%" (
  timeout /t 3 /nobreak >nul
  rmdir /s /q "%PROG%" 2>nul
)
if not "%DROP%"=="" (
  echo Removing %DROP% ...
  rmdir /s /q "%DROP%" 2>nul
)
echo.
if exist "%PROG%" (
  echo [warn] Some files could not be removed. Close any open windows
  echo        using that folder and delete it manually:
  echo        %PROG%
) else (
  echo Uninstall complete.
)
echo.
pause
exit /b

:abort
echo.
echo Cancelled.  Nothing was removed.
echo.
pause
