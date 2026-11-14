@echo off
rem Puts two shortcuts on YOUR Desktop: "Rooftop simulation" and "SCADA demo".
rem They point at start-rooftop.bat and start-scada-demo.bat in this folder. The shortcuts
rem are created on the Desktop only, never in the repository. Run again after moving the folder.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $w=New-Object -ComObject WScript.Shell; $root=(Get-Location).Path;" ^
  "foreach($s in @(@('Rooftop simulation','start-rooftop.bat','Start the rooftop SIMULATION page'),@('SCADA demo','start-scada-demo.bat','Start the SCADA replay demo'))){" ^
  " $l=$w.CreateShortcut((Join-Path $d ($s[0]+'.lnk'))); $l.TargetPath=(Join-Path $root $s[1]); $l.WorkingDirectory=$root;" ^
  " $l.Description=$s[2]; $l.IconLocation='%SystemRoot%\System32\shell32.dll,13'; $l.Save(); Write-Host ('Created: '+(Join-Path $d ($s[0]+'.lnk'))) }"
echo.
pause
endlocal
