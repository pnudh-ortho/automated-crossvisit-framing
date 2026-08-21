@echo off
setlocal enabledelayedexpansion

REM ---------------------------------------------------------------
REM  Uninstall.  Works even when the app will not start -- that is
REM  often exactly when you want to remove it.
REM
REM  A .bat cannot delete the folder it is running from (cmd holds a
REM  handle), so this copies itself to %TEMP% and re-runs from there.
REM  CRLF endings.  This file is UTF-8 and switches the console to
REM  code page 65001 before printing: the save locations are usually
REM  Korean, and in the system code page those lines vanished entirely.
REM  The original code page is restored before we finish.
REM ---------------------------------------------------------------

if "%~1"=="--stage2" goto stage2

cd /d "%~dp0"
set "PROG=%CD%"

REM -- Remember the console code page so we can put it back.
for /f "tokens=2 delims=:" %%c in ('chcp') do set "OLDCP=%%c"
chcp 65001 >nul

echo ===============================================
echo   Uninstall
echo ===============================================
echo.
echo   지울 폴더 : %PROG%
echo.
echo   기존 사진 폴더와 CRoCs 를 쓰며 저장한 위치에는 PATIENT DATA 가
echo   있습니다. 의료기록이 영구히 지워지는 것을 막기 위해, 삭제 과정에서
echo   이 위치들은 건드리지 않습니다. 더 필요하지 않으면 사용자가 직접
echo   탐색기에서 지워 주세요.
echo.
echo   현재 설정에 저장된 저장 위치:
set "FOUND="
for /f "usebackq delims=" %%a in (`powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; try { $j = ConvertFrom-Json (Get-Content -Raw -Encoding UTF8 -LiteralPath 'settings.json' -ErrorAction Stop); $l = @(); if ($j.root) { $l += $j.root }; if ($j.roots) { $l += $j.roots }; $s = @{}; foreach ($q in $l) { if ($q -and -not $s[$q]) { $s[$q] = 1; Write-Output $q } } } catch { }"`) do (
  echo       %%a
  set "FOUND=1"
)
if not defined FOUND echo       ^(settings.json 을 읽지 못했습니다^)
echo.

set /p GO="프로그램을 지울까요? (y/N): "
if /i not "%GO%"=="y" goto abort

REM -- Patient data is never deleted here. Medical records cannot be
REM    recovered from "rmdir /s /q" - it does not use the Recycle Bin -
REM    and a mistyped confirmation is not worth that risk.
REM    We print the path instead and let the user delete it in Explorer.

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
REM -- 콘솔을 원래 코드페이지로 되돌린 뒤 넘긴다
if defined OLDCP chcp %OLDCP% >nul
start "" cmd /c ""%TEMP%\acf_uninstall.bat" --stage2 "%PROG%" "!TOOLS!""
exit /b

:stage2
REM -- running from %TEMP% now; the program folder is free to delete
timeout /t 2 /nobreak >nul
set "PROG=%~2"
set "TOOLS=%~3"

REM -- desktop shortcut (both the plain and the OneDrive desktop)
del /f /q "%USERPROFILE%\Desktop\CRoCs Fastest Lap.lnk" 2>nul
del /f /q "%USERPROFILE%\OneDrive\Desktop\CRoCs Fastest Lap.lnk" 2>nul

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
if defined OLDCP chcp %OLDCP% >nul
echo.
echo Cancelled.  Nothing was removed.
echo.
pause
