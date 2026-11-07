@echo off
setlocal
set "COMPARISON_PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if exist "%COMPARISON_PYTHON%" goto launch
set "COMPARISON_PYTHON=C:\Venv\Comparsion\Scripts\pythonw.exe"
if exist "%COMPARISON_PYTHON%" goto launch
where pythonw.exe >nul 2>&1
if errorlevel 1 (
    echo Python GUI interpreter not found. Create .venv and install requirements-lock.txt.
    pause
    exit /b 1
)
set "COMPARISON_PYTHON=pythonw.exe"
:launch
start "" "%COMPARISON_PYTHON%" -B "%~dp0desktop.pyw" %*
