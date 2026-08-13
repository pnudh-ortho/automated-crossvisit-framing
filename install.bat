@echo off
setlocal enabledelayedexpansion

REM ---------------------------------------------------------------
REM  Bootstrap installer.  Download ONLY this file, run it, and it
REM  fetches everything else.
REM
REM    1. installs Git and Python if missing (winget)
REM    2. asks where to install (folder picker)
REM    3. clones the repository there
REM    4. creates the virtual environment
REM
REM  ASCII only, CRLF endings -- cmd reads .bat in the system code page,
REM  so Korean here would be parsed as commands.
REM ---------------------------------------------------------------

set "REPO=https://github.com/pnudh-ortho/automated-crossvisit-framing.git"
set "NAME=automated-crossvisit-framing"

echo ===============================================
echo   Ortho photo automation - installer
echo ===============================================
echo.

REM -- [1/4] Git ---------------------------------------------------
git --version >nul 2>&1
if not errorlevel 1 goto hasgit
echo [1/4] Git not found.
where winget >nul 2>&1
if errorlevel 1 goto manualgit
echo       Installing Git...
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto manualgit
echo.
echo       Git installed.  Close this window and run this file again
echo       so the new PATH takes effect.
echo.
pause
goto done
:manualgit
echo.
echo       Please install Git manually:  https://git-scm.com/download/win
echo.
pause
goto done
:hasgit
echo [1/4] Git found.

REM -- [2/4] Python ------------------------------------------------
REM The "python" under WindowsApps is a 2-byte Store stub that only
REM opens the Store, so probe the py launcher first.
set PYOK=0
py -3 --version >nul 2>&1
if not errorlevel 1 set PYOK=1
if "%PYOK%"=="0" (
  python --version >nul 2>&1
  if not errorlevel 1 set PYOK=1
)
if "%PYOK%"=="1" goto haspy
echo [2/4] Python not found.
where winget >nul 2>&1
if errorlevel 1 goto manualpy
echo       Installing Python 3.12...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto manualpy
echo.
echo       Python installed.  Close this window and run this file again
echo       so the new PATH takes effect.
echo.
pause
goto done
:manualpy
echo.
echo       Please install Python 3.10+ manually:
echo         https://www.python.org/downloads/
echo       Tick "Add python.exe to PATH" during installation.
echo.
pause
goto done
:haspy
echo [2/4] Python found.

REM -- [3/4] where to install --------------------------------------
echo [3/4] Choose where to install.
echo       A folder picker will open.  The program goes into a
echo       "%NAME%" subfolder of what you pick.
echo.
for /f "usebackq delims=" %%d in (`powershell -NoProfile -STA -Command ^
  "Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description = 'Choose the install location'; $f.RootFolder = 'MyComputer'; $f.SelectedPath = 'C:\'; if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }"`) do set "BASE=%%d"

if not defined BASE (
  echo       Cancelled.  Nothing was installed.
  echo.
  pause
  goto done
)
set "DEST=%BASE%\%NAME%"
echo       Installing to: %DEST%

if exist "%DEST%\.git" (
  echo       Already installed there - updating instead.
  pushd "%DEST%"
  git pull --ff-only
  popd
  goto venv
)
if exist "%DEST%" (
  echo.
  echo [error] That folder already exists and is not an installation:
  echo         %DEST%
  echo.
  pause
  goto done
)

echo       Downloading...
git clone --depth 1 "%REPO%" "%DEST%"
if errorlevel 1 (
  echo.
  echo [error] Download failed.  Check your internet connection.
  echo.
  pause
  goto done
)

REM -- [4/4] virtual environment ------------------------------------
:venv
echo [4/4] Creating virtual environment and installing dependencies...
echo       (a few minutes on first run)
pushd "%DEST%"
if exist ".venv\Scripts\python.exe" goto haveenv
py -3 -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  popd
  echo [error] Could not create the virtual environment.
  pause
  goto done
)
:haveenv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r webapp\requirements.txt
if errorlevel 1 (
  popd
  echo.
  echo [error] Failed to install dependencies.  Check your internet connection.
  echo.
  pause
  goto done
)
popd

REM -- desktop shortcut (optional) ----------------------------------
set /p MKSC="Create a desktop shortcut (CRoCs)? (Y/n): "
if /i "%MKSC%"=="n" goto noshortcut
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\CRoCs.lnk'); $s.TargetPath='%DEST%\run.bat'; $s.WorkingDirectory='%DEST%'; $s.IconLocation='%DEST%\assets\crocs.ico'; $s.Save()" >nul 2>&1
echo       Desktop shortcut created: CRoCs
:noshortcut

echo.
echo ===============================================
echo   Installed.
echo ===============================================
echo.
echo   Folder : %DEST%
echo.
echo   Next:
echo     1. Put the model and template files (about 400 MB) into
echo          %DEST%\models
echo        The app tells you which files are missing.
echo     2. Run  %DEST%\run.bat
echo        (or the CRoCs desktop shortcut, if you created it)
echo.
set /p OPENIT="Open the models folder now? (Y/n): "
if /i not "%OPENIT%"=="n" start "" "%DEST%\models"
echo.
pause

:done
endlocal
