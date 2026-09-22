@echo off
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%offload_models.ps1" %*
if not "%ERRORLEVEL%"=="0" (
    echo.
    echo OfflineAI model offload failed. The error above is also recorded on screen.
    pause
)
