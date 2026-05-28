@echo off
rem agent-teams — after-pull bring-up launcher for Windows.
rem
rem Launches bring-up.ps1 with -ExecutionPolicy Bypass so it runs on a fresh
rem Windows machine regardless of the default Restricted PowerShell policy.
rem Batch files are NOT subject to ExecutionPolicy — this is the zero-friction
rem entry point for new Windows installs.
rem
rem Usage: bin\bring-up.cmd [--force equivalent not available via .cmd; use .ps1 -Force directly]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bring-up.ps1" %*
exit /b %ERRORLEVEL%
