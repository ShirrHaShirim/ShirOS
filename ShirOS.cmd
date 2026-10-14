@echo off
rem One-click ShirOS runtime control.
rem Usage: ShirOS.cmd start^|stop^|restart^|status^|check^|install^|uninstall^|store-key^|clear-key^|test-key^|logs
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\ShirosRuntime.ps1" %*
exit /b %ERRORLEVEL%
