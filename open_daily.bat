@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" scripts\open_report.py --latest
if errorlevel 1 pause
