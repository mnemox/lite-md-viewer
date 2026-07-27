@echo off
setlocal

::  Starts LiteMdViewer and opens the browser once the server answers.
::
::    run.bat                 start on the default port (5099)
::    run.bat 5100            start on a specific port
::    run.bat --no-browser    start without opening a browser
::    run.bat --setup         (re)create the virtualenv and install dependencies, then start
::    run.bat --setup-ai      (re)run the local analysis wizard (Ollama + Gemma 4), then start
::    run.bat --no-ai         skip local analysis for this launch only
::
::  The server runs in this window, so its log is visible here and Ctrl+C stops it.

set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

set "APPDIR=%REPO%\src\litemd_viewer"
set "PY=%REPO%\.venv\Scripts\python.exe"
set "AI_MARKER=%REPO%\.venv\ai-setup.txt"
set "AI_MODEL=gemma4:e2b"

set "PORT_ARG="
set "OPEN_BROWSER=1"
set "SETUP="
set "SETUP_AI="
set "NO_AI="

:: ---------------------------------------------------------------- arguments
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--no-browser" goto arg_nobrowser
if /i "%~1"=="--setup"      goto arg_setup
if /i "%~1"=="--setup-ai"   goto arg_setup_ai
if /i "%~1"=="--no-ai"      goto arg_noai
if /i "%~1"=="-h"           goto usage
if /i "%~1"=="--help"       goto usage
echo %~1| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 goto arg_bad
set "PORT_ARG=%~1"
shift
goto parse

:arg_nobrowser
set "OPEN_BROWSER=0"
shift
goto parse

:arg_setup
set "SETUP=1"
shift
goto parse

:arg_setup_ai
set "SETUP_AI=1"
shift
goto parse

:arg_noai
set "NO_AI=1"
shift
goto parse

:arg_bad
echo Unrecognised argument: %~1
echo.
goto usage

:parsed
if not exist "%APPDIR%\app\main.py" (
  echo Could not find the application at:
  echo     %APPDIR%\app\main.py
  echo.
  echo Run this script from the repository root.
  pause
  exit /b 1
)

:: PORT is only exported when asked for, so the app keeps its own default otherwise.
if defined PORT_ARG (
  set "LISTEN_PORT=%PORT_ARG%"
  set "PORT=%PORT_ARG%"
) else (
  set "LISTEN_PORT=5099"
)
set "URL=http://127.0.0.1:%LISTEN_PORT%"

:: ---------------------------------------------------------------- environment
if defined SETUP goto setup
if exist "%PY%" goto ready

echo No virtual environment was found at %REPO%\.venv
echo.
choice /c YN /n /m "Create it and install the dependencies now? [Y/N] "
if errorlevel 2 exit /b 1
echo.

:setup
if exist "%PY%" goto deps

where py >nul 2>&1
if errorlevel 1 (set "BOOTSTRAP=python") else (set "BOOTSTRAP=py -3")

echo Creating the virtual environment...
%BOOTSTRAP% -m venv "%REPO%\.venv"
if errorlevel 1 (
  echo.
  echo Could not create the virtual environment. Is Python 3.11+ installed and on PATH?
  pause
  exit /b 1
)

:deps
echo Installing dependencies. The first run downloads a few hundred MB.
:: Install from the repository root: requirements.txt refers to ./src/local-vector by
:: relative path, and pip resolves that against the current directory.
pushd "%REPO%"
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r "src\litemd_viewer\requirements.txt"
set "PIP_STATUS=%errorlevel%"
popd
if not "%PIP_STATUS%"=="0" (
  echo.
  echo Dependency installation failed.
  pause
  exit /b 1
)
echo.

:ready
:: --------------------------------------------------------------- local analysis
if "%NO_AI%"=="1" goto ai_done
call :setup_ai
if exist "%AI_MARKER%" (
  for /f "usebackq delims=" %%m in ("%AI_MARKER%") do set "AI_MARKER_VALUE=%%m"
  if not "%AI_MARKER_VALUE%"=="disabled" (
    set "LITEMD_AI_ENABLED=1"
    set "LITEMD_OLLAMA_MODEL=%AI_MARKER_VALUE%"
  )
)
:ai_done

:: ------------------------------------------------------------- already running?
:: Double-clicking this script means "give me a fresh instance", so any live one is
:: replaced rather than handed over to. It is asked to stop over HTTP first: a forced kill
:: skips the shutdown that commits the vector index, and that file is written in place, so
:: being terminated mid-write can leave search broken until the index is deleted by hand.
:: Only a server that fails to go away in time is killed outright.
::
:: Nothing else is ever killed: the port has to answer as LiteMdViewer before this touches
:: the process holding it.
call :stop_instance
if errorlevel 2 (
  echo Port %LISTEN_PORT% is in use by something that is not LiteMdViewer.
  echo Free the port, or start on another one:  run.bat 5100
  pause
  exit /b 1
)
if errorlevel 1 (
  echo Could not stop the instance already on port %LISTEN_PORT%.
  pause
  exit /b 1
)

:: ---------------------------------------------------------------- launch
:: This script opens the tab itself, so the app must not also honour
:: openBrowserOnStart -- otherwise a browser would open twice.
set "LITEMD_NO_BROWSER=1"

if "%OPEN_BROWSER%"=="1" start "" /min powershell -NoProfile -Command "for ($i=0; $i -lt 120; $i++) { try { if ((Invoke-WebRequest -UseBasicParsing -Uri '%URL%/api/search/status' -TimeoutSec 2).StatusCode -eq 200) { Start-Process '%URL%'; exit } } catch { }; Start-Sleep -Milliseconds 500 }"

echo Starting LiteMdViewer on %URL%
echo Press Ctrl+C to stop.
echo.

cd /d "%APPDIR%" || (
  echo Could not open %APPDIR%
  pause
  exit /b 1
)
"%PY%" -m app.main
exit /b %errorlevel%

:usage
echo Usage: run.bat [port] [--no-browser] [--setup] [--setup-ai] [--no-ai]
echo.
echo   port           port to listen on (default 5099)
echo   --no-browser   start the server without opening a browser
echo   --setup        (re)create the virtualenv and install dependencies first
echo   --setup-ai     (re)run the local analysis wizard: installs Ollama and pulls
echo                  %AI_MODEL% for chatting with the model about an open document
echo   --no-ai        skip local analysis for this launch only, even if set up
exit /b 1

:: ----------------------------------------------------------- stop_instance
:: Clears port LISTEN_PORT for a new instance.
::   0  the port was free, or its owner has stopped
::   1  it is ours, but it would not stop
::   2  the port belongs to something that is not LiteMdViewer -- left untouched
:stop_instance
powershell -NoProfile -Command ^
  "$port = %LISTEN_PORT%;" ^
  "$conn = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue);" ^
  "if (-not $conn) { exit 0 };" ^
  "$base = 'http://127.0.0.1:' + $port;" ^
  "$ours = $false;" ^
  "try { $ours = (Invoke-WebRequest -UseBasicParsing -Uri ($base + '/api/search/status') -TimeoutSec 5).StatusCode -eq 200 } catch { };" ^
  "if (-not $ours) { exit 2 };" ^
  "Write-Host ('Stopping the instance already on port ' + $port + '...');" ^
  "try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri ($base + '/api/shutdown') -TimeoutSec 5 | Out-Null } catch { };" ^
  "for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Milliseconds 250; if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { exit 0 } };" ^
  "Write-Host 'It did not stop in time; forcing it.';" ^
  "foreach ($owner in ($conn | Select-Object -ExpandProperty OwningProcess -Unique)) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue };" ^
  "for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 250; if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { exit 0 } };" ^
  "exit 1"
exit /b %errorlevel%

:: --------------------------------------------------------------- setup_ai
:: Opt-in local analysis: installs Ollama (if missing) and pulls %AI_MODEL%, so the app can
:: chat with the reader about whatever document is open. Entirely local -- nothing the
:: document contains ever leaves the machine.
::
:: The decision (and, on success, the model tag) is cached in %AI_MARKER% so this is asked
:: at most once. --setup-ai re-runs it on demand; a failed pull still writes "disabled" so a
:: broken Ollama install does not re-prompt on every future launch -- --setup-ai retries it.
:setup_ai
if "%SETUP_AI%"=="1" goto ai_ask
if exist "%AI_MARKER%" goto :eof

:ai_ask
echo.
choice /c YN /n /m "Set up the local analysis system? This installs Ollama and downloads Gemma 4 (~10 GB). [Y/N] "
if errorlevel 2 (
  echo disabled> "%AI_MARKER%"
  goto :eof
)
echo.

where ollama >nul 2>&1
if errorlevel 1 if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama"
where ollama >nul 2>&1
if not errorlevel 1 goto ai_serve

echo Installing Ollama...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://ollama.com/install.ps1 | iex"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama"
where ollama >nul 2>&1
if errorlevel 1 (
  echo.
  echo Could not find or install Ollama. Skipping local analysis for now.
  echo Retry any time with:  run.bat --setup-ai
  echo disabled> "%AI_MARKER%"
  goto :eof
)

:ai_serve
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto ai_pull

echo Starting Ollama...
start "" /min ollama serve
powershell -NoProfile -Command ^
  "for ($i = 0; $i -lt 40; $i++) { try { if ((Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2).StatusCode -eq 200) { exit 0 } } catch { }; Start-Sleep -Milliseconds 500 }; exit 1"
if errorlevel 1 (
  echo.
  echo Ollama did not start in time. Skipping local analysis for now.
  echo Retry any time with:  run.bat --setup-ai
  echo disabled> "%AI_MARKER%"
  goto :eof
)

:ai_pull
echo Downloading %AI_MODEL% ^(this can take a while on the first run^)...
ollama pull %AI_MODEL%
if errorlevel 1 (
  echo.
  echo Could not download %AI_MODEL%. Skipping local analysis for now.
  echo Retry any time with:  run.bat --setup-ai
  echo disabled> "%AI_MARKER%"
  goto :eof
)

echo %AI_MODEL%> "%AI_MARKER%"
echo.
echo Local analysis is ready.
goto :eof
