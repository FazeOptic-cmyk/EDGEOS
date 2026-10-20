@echo off
setlocal
title EDGEOS Windows installer builder
cd /d "%~dp0"
where pwsh.exe >nul 2>&1
if errorlevel 1 goto missing_powershell

pwsh.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0scripts\build-local-windows.ps1"
set "EDGEOS_BUILD_EXIT=%ERRORLEVEL%"
echo.
if not "%EDGEOS_BUILD_EXIT%"=="0" echo Build did not complete. Keep the error text and check the logs folder.
pause
exit /b %EDGEOS_BUILD_EXIT%

:missing_powershell
echo PowerShell 7 is required for this one-time build.
echo Open Windows Terminal and install the build tools:
echo.
echo winget install --exact --id Microsoft.PowerShell --source winget
echo winget install --exact --id OpenJS.NodeJS.22 --source winget
echo winget install --exact --id astral-sh.uv --source winget
echo.
echo Close and reopen this launcher after installation.
echo This launcher does not install anything or change billing on its own.
pause
exit /b 1
