@echo off
setlocal

:: Starts LiteMdViewer and opens the browser.
:: Looks for the app next to this script, then in the repo layout, then a fallback path.

set "APPDIR=%~dp0"
if exist "%APPDIR%app\main.py" goto found

set "APPDIR=%~dp0src\litemd_viewer\"
if exist "%APPDIR%app\main.py" goto found

set "APPDIR=D:\Sources\lite-md-viewer\src\litemd_viewer\"
if exist "%APPDIR%app\main.py" goto found

echo Could not find the LiteMdViewer application.
echo.
echo Place this batch file next to the litemd_viewer folder,
echo or edit the fallback path inside this script.
pause
exit /b 1

:found
:: The repository root is two levels above the app directory.
for %%I in ("%APPDIR%..\..") do set "REPO=%%~fI"

set "PY=%REPO%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Virtual environment not found at %REPO%\.venv
  echo.
  echo Create it first:
  echo     py -3 -m venv .venv
  echo     .venv\Scripts\python -m pip install -r src\litemd_viewer\requirements.txt
  pause
  exit /b 1
)

cd /d "%APPDIR%" || (
  echo Failed to open the application directory: %APPDIR%
  pause
  exit /b 1
)

echo Starting LiteMdViewer in a new window...
start "LiteMd Viewer" "%PY%" -m app.main

echo Waiting a few seconds for the server to start...
timeout /t 5 /nobreak >nul

echo Opening http://localhost:5099 ...
start http://localhost:5099
