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

copy /y "%~f0" "%TEMP%\acf_uninstall.bat" >nul
start "" cmd /c ""%TEMP%\acf_uninstall.bat" --stage2 "%PROG%" "!DROP!""
exit /b

:stage2
REM -- running from %TEMP% now; the program folder is free to delete
timeout /t 2 /nobreak >nul
set "PROG=%~2"
set "DROP=%~3"
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
