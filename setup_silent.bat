@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem 给豆包/自动化用：不暂停，失败用退出码表示
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
exit /b %errorlevel%
