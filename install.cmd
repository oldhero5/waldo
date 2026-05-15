@echo off
REM Waldo one-shot installer for Windows (cmd.exe).
REM
REM Usage:
REM   curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM This is a thin wrapper that downloads install.ps1 and runs it via
REM PowerShell, bypassing the local execution policy for this one call.

setlocal

set "WALDO_BRANCH=main"
set "WALDO_PS1_URL=https://raw.githubusercontent.com/oldhero5/waldo/%WALDO_BRANCH%/install.ps1"
set "WALDO_TMP=%TEMP%\waldo-install-%RANDOM%.ps1"

where powershell >nul 2>&1
if errorlevel 1 (
    echo Error: PowerShell is not available on PATH.
    echo Install PowerShell or use the install.ps1 method directly.
    exit /b 1
)

echo Downloading installer to %WALDO_TMP%...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%WALDO_PS1_URL%' -OutFile '%WALDO_TMP%'"
if errorlevel 1 (
    echo Failed to download %WALDO_PS1_URL%
    exit /b 1
)

echo Running installer...
powershell -NoProfile -ExecutionPolicy Bypass -File "%WALDO_TMP%" %*
set "RC=%ERRORLEVEL%"

del /q "%WALDO_TMP%" >nul 2>&1
exit /b %RC%
