@echo off
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Python environment not found. Install PV1 dependencies first.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0tools\ais_rzn_launcher.py"
exit /b 0
