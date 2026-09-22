@echo off
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start_usb.ps1" %*
if not "%ERRORLEVEL%"=="0" (
    echo.
    echo OfflineAI failed to start. The error above is also recorded in the logs folder.
    pause
)
