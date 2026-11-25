@echo off
cd /d "%~dp0"
set "PS32=C:\Program Files (x86)\PowerShell\7\pwsh.exe"
set "PS64=C:\Program Files\PowerShell\7\pwsh.exe"
if not exist "%PS32%" (
  echo 32-bit PowerShell 7 is not installed.
  pause
  exit /b 1
)
"%PS32%" -NoLogo -NoProfile -Sta -ExecutionPolicy Bypass -File "%~dp0u8co-probe.ps1" %*
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
