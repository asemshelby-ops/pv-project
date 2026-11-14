@echo off
rem Puts the shortcut "PV SCADA" on YOUR Desktop, pointing at start-scada.bat in this folder.
rem The shortcut is created on the Desktop only, never in the repository. It also removes the
rem two older shortcuts ("Rooftop simulation", "SCADA demo") this script used to create.
rem It never switches git branches: the shortcut runs whatever branch is checked out (main).
rem Run again after moving the folder. setup-windows.bat calls it with /nopause.
rem Exit code 0 when the shortcut was created, 1 otherwise.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $d=[Environment]::GetFolderPath('Desktop'); $root=(Get-Location).Path;" ^
  "foreach($old in 'Rooftop simulation.lnk','SCADA demo.lnk'){ $p=Join-Path $d $old; if(Test-Path $p){ Remove-Item $p; Write-Host ('Removed: '+$p) } }" ^
  "$w=New-Object -ComObject WScript.Shell; $l=$w.CreateShortcut((Join-Path $d 'PV SCADA.lnk'));" ^
  "$l.TargetPath=(Join-Path $root 'start-scada.bat'); $l.WorkingDirectory=$root;" ^
  "$l.Description='Start the PV SCADA: real module and rooftop SIMULATION'; $l.IconLocation='%SystemRoot%\System32\shell32.dll,13';" ^
  "$l.Save(); Write-Host ('Created: '+(Join-Path $d 'PV SCADA.lnk'))"
set "RC=0"
if errorlevel 1 set "RC=1"
if /i not "%~1"=="/nopause" (
  echo.
  pause
)
endlocal & exit /b %RC%
