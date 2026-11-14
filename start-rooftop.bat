@echo off
rem Rooftop SIMULATION (digital twin, 120 simulated modules) on http://127.0.0.1:8050/rooftop
rem Double-click to start; close the window to stop. See docs/scada/README.md.
setlocal
title Rooftop SIMULATION is running - close this window to stop
set "URL=http://127.0.0.1:8050/rooftop"
cd /d "%~dp0"

rem --- 1. git: only switch to twin-sim when no tracked file has uncommitted changes ------------
where git >nul 2>&1 || (echo ERROR: git was not found on PATH. & goto :fail)
set "DIRTY="
for /f "delims=" %%L in ('git status --porcelain --untracked-files^=no 2^>nul') do set "DIRTY=1"
if defined DIRTY (
  echo WARNING: there are uncommitted changes in this folder:
  git status --short --untracked-files=no
  echo.
  echo Nothing was switched and nothing was started. Commit or stash the changes, then run this again.
  goto :fail
)
set "BRANCH="
for /f "delims=" %%B in ('git branch --show-current') do set "BRANCH=%%B"
if /i not "%BRANCH%"=="twin-sim" (
  echo Switching from branch "%BRANCH%" to twin-sim ...
  git checkout twin-sim || (echo ERROR: could not switch to twin-sim. & goto :fail)
)

rem --- 2. Python of the project ------------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found. Create it once, in this folder:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -r requirements.txt
  goto :fail
)

rem --- open the browser as soon as the server answers (waits up to 3 min, in the background) ---
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='%URL%'; for($i=0; $i -lt 180; $i++){ try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $u | Out-Null; Start-Process $u; exit } catch { Start-Sleep -Seconds 1 } }"

echo.
echo ============================================================================
echo   Rooftop SIMULATION is running
echo   The page opens in your browser at %URL% once the server has started.
echo.
echo   CLOSE THIS WINDOW TO STOP IT.
echo ============================================================================
echo.
".venv\Scripts\python.exe" -m scada.run --sim
echo.
echo The SCADA has stopped. If it stopped at once, read the message above
echo (for example: port 8050 busy because another SCADA window is still open).

:fail
echo.
pause
endlocal
