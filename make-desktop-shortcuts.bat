@echo off
rem Puts the shortcut "PV SCADA" on YOUR Desktop, pointing at start-scada.bat in this folder.
rem The shortcut is created on the Desktop only, never in the repository. It also removes the
rem two older shortcuts ("Rooftop simulation", "SCADA demo") this script used to create.
rem Run again after moving the folder.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $root=(Get-Location).Path;" ^
  "foreach($old in 'Rooftop simulation.lnk','SCADA demo.lnk'){ $p=Join-Path $d $old; if(Test-Path $p){ Remove-Item $p; Write-Host ('Removed: '+$p) } }" ^
  "$w=New-Object -ComObject WScript.Shell; $l=$w.CreateShortcut((Join-Path $d 'PV SCADA.lnk'));" ^
  "$l.TargetPath=(Join-Path $root 'start-scada.bat'); $l.WorkingDirectory=$root;" ^
  "$l.Description='Start the PV SCADA: real module and rooftop SIMULATION'; $l.IconLocation='%SystemRoot%\System32\shell32.dll,13';" ^
  "$l.Save(); Write-Host ('Created: '+(Join-Path $d 'PV SCADA.lnk'))"
echo.
pause
endlocal
