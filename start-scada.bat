@echo off
rem PV SCADA: the real module's source AND the rooftop SIMULATION, in one server.
rem Double-click to start; close the window to stop. See docs/scada/README.md.
rem
rem Real-module source:
rem   - LIVE when the file scada-live.cfg exists in this folder. Its first line holds the
rem     arguments for the logger, e.g.   --folder D:\sd_copy --serial COM5
rem     (the file stays on this computer: it is ignored by git)
rem   - otherwise the REPLAY demo (synthetic logger files, 600x).
rem The rooftop SIMULATION (--sim) always runs next to it, with its own file and its own page.
setlocal
title PV SCADA is running - close this window to stop
set "URL=http://127.0.0.1:8050"
cd /d "%~dp0"

rem --- 1. git: use whatever branch is checked out (main); this script never switches branches ---
rem A folder from GitHub's "Download ZIP" has no .git: run its files as they are, no git needed.
if not exist ".git" goto :nogit
where git >nul 2>&1 || goto :python
set "BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "BRANCH=%%B"
if defined BRANCH echo Using the checked-out branch "%BRANCH%" ^(no branch switch^).
goto :python
:nogit
echo No git repository in this folder ^(a "Download ZIP" copy^): using its files as they are.

:python
:nogit
echo No git repository in this folder ^(a "Download ZIP" copy^): using its files as they are.

:python

rem --- 2. Python of the project ------------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found. Double-click setup-windows.bat in this
  echo folder once ^(it creates .venv and installs the packages^), then start again.
  goto :fail
)

rem --- 3. the real module's source: live (scada-live.cfg) or the replay demo -------------------
set "REAL_ARGS="
if exist "scada-live.cfg" set /p REAL_ARGS=<"scada-live.cfg"
if defined REAL_ARGS (
  set "REAL_NOTE=LIVE: %REAL_ARGS%"
  goto :start
)
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
set "REAL_ARGS=--replay --speed 600"
set "REAL_NOTE=REPLAY demo of synthetic logger files at 600x (create scada-live.cfg for live data)"

:start
rem --- open the dashboard as soon as the server answers (waits up to 3 min, in the background) --
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='%URL%'; for($i=0; $i -lt 180; $i++){ try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $u | Out-Null; Start-Process $u; exit } catch { Start-Sleep -Seconds 1 } }"

echo.
echo ============================================================================
echo   PV SCADA is running
echo     Real module : %REAL_NOTE%
echo     Rooftop     : SIMULATION (digital twin), page %URL%/rooftop
echo   The dashboard opens in your browser at %URL% once the server has started;
echo   the bar at the top of each page switches between the two.
echo.
echo   CLOSE THIS WINDOW TO STOP IT.
echo ============================================================================
echo.
".venv\Scripts\python.exe" -m scada.run %REAL_ARGS% --sim
echo.
echo The SCADA has stopped. If it stopped at once, read the message above
echo (for example: port 8050 busy because another SCADA window is still open).

:fail
echo.
pause
endlocal
