@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo 正在启动昨天的群日报...
"%PY%" scripts\start_daily.py --date yesterday
echo.
echo 已启动。采集期间不要操作微信，约 15-25 分钟。
echo 采集完成后，让豆包继续整理；报告生成后再打开。
pause
