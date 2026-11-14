@echo off
rem One-time setup for a teammate who has only Python (no git). Double-click this file.
rem Works in a folder unzipped from GitHub's "Download ZIP" (for example pv-project-twin-sim).
rem   1. checks that Python 3.12 or newer, 64-bit, is on PATH
rem      (the pinned packages in requirements.txt have no Windows wheels for 3.10/3.11 or 32-bit)
rem   2. creates .venv in this folder
rem   3. installs requirements.txt into .venv
rem   4. runs make-desktop-shortcuts.bat: "PV SCADA" on your Desktop
rem Safe to run again (for example after a failed install, or in a newly downloaded copy).
setlocal
title PV project setup
cd /d "%~dp0"

rem --- 0. are we in the unzipped project folder? ------------------------------------------------
if not exist "requirements.txt" goto :notunzipped
if not exist "start-scada.bat" goto :notunzipped
if not exist "make-desktop-shortcuts.bat" goto :notunzipped

echo.
echo === PV project setup ======================================================
echo Folder: %CD%
echo.

rem --- 1. Python 3.12+ (64-bit) on PATH ----------------------------------------------------------
echo [1/4] Looking for Python on PATH ...
python -c "import sys" >nul 2>&1
if errorlevel 1 goto :nopython
set "PYVER="
for /f "delims=" %%V in ('python -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%V"
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)"
if errorlevel 1 goto :oldpython
python -c "import sys; sys.exit(0 if sys.maxsize.bit_length() == 63 else 1)"
if errorlevel 1 goto :32bit
echo       found Python %PYVER% (64-bit).

rem --- 2. .venv ---------------------------------------------------------------------------------
echo.
echo [2/4] Virtual environment .venv ...
if not exist ".venv\Scripts\python.exe" goto :makevenv
rem An existing .venv must still run and be 3.12+ (a .venv copied from another PC does not).
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
  echo       .venv already exists and works; keeping it.
  goto :install
)
echo       the existing .venv does not work here; making a new one.
rmdir /s /q ".venv"
:makevenv
python -m venv .venv
if errorlevel 1 goto :venvfail
if not exist ".venv\Scripts\python.exe" goto :venvfail
echo       created .venv

rem --- 3. requirements --------------------------------------------------------------------------
:install
echo.
echo [3/4] Installing the packages of requirements.txt (needs internet; can take several minutes) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 echo       pip could not upgrade itself; continuing with the version it has.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :pipfail
echo       packages installed.

rem --- 4. Desktop shortcut ----------------------------------------------------------------------
echo.
echo [4/4] Creating the Desktop shortcut ...
call "make-desktop-shortcuts.bat" /nopause
if errorlevel 1 goto :shortcutfail

echo.
echo ============================================================================
echo   Done: double-click PV SCADA on your Desktop
echo.
echo   The first start builds the demo data (answer Y; it takes a few minutes),
echo   then the dashboard opens in your browser. Close the black window to stop it.
echo   If you move or re-download this folder, run setup-windows.bat again there.
echo ============================================================================
goto :end

rem --- errors -----------------------------------------------------------------------------------
:notunzipped
echo.
echo ERROR: this file must be run from the unzipped project folder.
echo   Right-click the downloaded ZIP, choose "Extract All...", open the extracted folder
echo   (it contains requirements.txt and start-scada.bat) and double-click
echo   setup-windows.bat there. Running it from inside the ZIP does not work.
goto :end

:nopython
echo.
echo ERROR: Python was not found on PATH.
echo   1. Download the "Windows installer (64-bit)" of Python 3.12 or newer from
echo        https://www.python.org/downloads/windows/
echo   2. On the FIRST screen of the installer tick
echo        "Add python.exe to PATH"   (the box at the bottom)
echo      then click "Install Now".
echo   3. Close this window and double-click setup-windows.bat again.
echo   If it still says "not found": Windows Settings, search "App execution aliases",
echo   and switch OFF the two entries "python.exe" and "python3.exe" (App Installer).
goto :end

:oldpython
echo.
echo ERROR: Python %PYVER% was found, but this project needs Python 3.12 or newer
echo   (its pinned packages have no Windows versions for older Python).
echo   Install the newest Python from https://www.python.org/downloads/windows/
echo   and tick "Add python.exe to PATH" on the first screen of the installer.
echo   Then close this window and double-click setup-windows.bat again.
echo   If "python" still finds the old version, uninstall it first (Settings, Apps).
goto :end

:32bit
echo.
echo ERROR: Python %PYVER% is the 32-bit version; the project's packages need 64-bit.
echo   Uninstall it (Settings, Apps), install the "Windows installer (64-bit)" from
echo   https://www.python.org/downloads/windows/ and tick "Add python.exe to PATH".
echo   Then close this window and double-click setup-windows.bat again.
goto :end

:venvfail
echo.
echo ERROR: could not create .venv in %CD%
echo   Check that this folder is not read-only and that the disk is not full,
echo   then run setup-windows.bat again.
goto :end

:pipfail
echo.
echo ERROR: installing the packages failed (see the red lines above).
echo   Most often: no internet, or a network that blocks pip (some university
echo   networks do: try a phone hotspot). Then run setup-windows.bat again;
echo   what was already installed is kept.
goto :end

:shortcutfail
echo.
echo ERROR: the Desktop shortcut could not be created (see the message above).
echo   Everything else is installed. You can start the SCADA by double-clicking
echo   start-scada.bat in this folder instead.
goto :end

:end
echo.
pause
endlocal
