@echo off
rem SMSocket kernel launcher - cmd.exe entry point.
rem usage: start.cmd [--port 8011] [--config config.yaml] [any run.py flag]
setlocal
cd /d "%~dp0"
set "PY=%SMSSOCKET_PYTHON%"
if "%PY%"=="" if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if "%PY%"=="" set "PY=python"
echo [start] %PY% run.py %*
"%PY%" run.py %*
endlocal
