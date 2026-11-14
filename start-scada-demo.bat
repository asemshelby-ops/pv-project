@echo off
rem SCADA replay demo (synthetic logger files, 600x) on http://127.0.0.1:8050
rem Double-click to start; close the window to stop. See docs/scada/README.md.
setlocal
title SCADA REPLAY demo is running - close this window to stop
set "URL=http://127.0.0.1:8050"
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

rem --- 3. the demo data set (synthetic logger files), built once if missing --------------------
if not exist "out\scada\demo_source\" (
  echo The replay demo data set out\scada\demo_source is missing. It is built once from the
  echo SYNTHETIC run of the paper pipeline:
  echo   .venv\Scripts\python -m analysis.demo_synth   ^(only if out\paper_demo\seed5 is missing^)
  echo   .venv\Scripts\python -m scada.demo_data
  choice /c YN /m "Build it now (can take several minutes)"
  if errorlevel 2 goto :fail
  if not exist "out\paper_demo\seed5\" (
    ".venv\Scripts\python.exe" -m analysis.demo_synth || (echo ERROR: analysis.demo_synth failed. & goto :fail)
  )
  ".venv\Scripts\python.exe" -m scada.demo_data || (echo ERROR: scada.demo_data failed. & goto :fail)
)

rem --- open the browser as soon as the server answers (waits up to 3 min, in the background) ---
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='%URL%'; for($i=0; $i -lt 180; $i++){ try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $u | Out-Null; Start-Process $u; exit } catch { Start-Sleep -Seconds 1 } }"

echo.
echo ============================================================================
echo   SCADA REPLAY demo is running
echo   The page opens in your browser at %URL% once the server has started.
echo.
echo   CLOSE THIS WINDOW TO STOP IT.
echo ============================================================================
echo.
".venv\Scripts\python.exe" -m scada.run --replay --speed 600
echo.
echo The SCADA has stopped. If it stopped at once, read the message above
echo (for example: port 8050 busy because another SCADA window is still open).

:fail
echo.
pause
endlocal
