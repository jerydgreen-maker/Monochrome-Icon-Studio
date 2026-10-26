@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python launcher was not found.
  echo Install Python 3 and enable the Python launcher.
  pause
  exit /b 1
)

py -3 -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo ERROR: Could not install the required packages.
  pause
  exit /b 1
)

py -3 icon_studio.py
if errorlevel 1 (
  echo.
  echo Icon Studio exited with an error.
  pause
)

endlocal
