@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Python runtime missing. Keep this BAT in the installation folder.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0publish_html.py" --check "%~1"
set "PUBLISH_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %PUBLISH_EXIT%
