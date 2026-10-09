@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run the initialization BAT first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0login_codex.py"
set "LOGIN_EXIT=%ERRORLEVEL%"
pause
exit /b %LOGIN_EXIT%
