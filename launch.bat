@echo off
REM MeowCam Bridge - Windows launcher
REM Double-click to start. Keeps console open for logs.

echo ==========================================
echo   MeowCam Bridge - Starting up...
echo ==========================================
echo.

REM --- Check for Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.11 or newer from:
    echo   https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%a in ('python --version 2^>^&1') do set PYTHON_VERSION=%%a
echo Found: %PYTHON_VERSION%
echo.

REM --- Install / update package ---
if exist "pyproject.toml" (
    echo Installing MeowCam Bridge...
    python -m pip install -e ".[dev]" --quiet
    if errorlevel 1 (
        echo WARNING: pip install had an issue. Trying to run anyway...
    ) else (
        echo OK.
    )
) else (
    echo No pyproject.toml found. Assuming package is already installed.
)
echo.

REM --- Config file ---
set CONFIG_FILE=meowcam-bridge.json
if not exist "%CONFIG_FILE%" (
    echo No config found. A default config will be created on first run.
    echo You can edit %CONFIG_FILE% later or use the Settings tab in the web UI.
    echo.
)

REM --- Firewall warning ---
echo IMPORTANT: Windows Firewall may ask to allow Python network access.
echo Please click ALLOW so the bridge can send/receive UDP packets.
echo.

REM --- Start bridge ---
echo Starting MeowCam Bridge...
echo Web UI will be available at: http://localhost:8080
echo.
echo Press Ctrl+C to stop.
echo.

REM Open browser after a short delay so the server is ready
start /b cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8080"

REM Run the bridge. --host 0.0.0.0 lets other machines on the LAN reach the UI.
python -m meowcam_bridge --config "%CONFIG_FILE%" --host 0.0.0.0 --port 8080

REM If we get here, the bridge exited.
echo.
echo MeowCam Bridge has stopped.
pause