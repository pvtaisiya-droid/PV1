@echo off
setlocal
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"
set "PREVIEW_PYTHON=%~dp0.venv\Scripts\python.exe"
if defined PV1_PYTHON set "PREVIEW_PYTHON=%PV1_PYTHON%"
"%PREVIEW_PYTHON%" tools\acceptance_stand.py start --root "%~dp0output\demo"
exit /b %ERRORLEVEL%
