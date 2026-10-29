@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   微信群日报 - 安装
echo ========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
set "CODE=%errorlevel%"

echo.
if "%CODE%"=="0" (
  echo 安装完成。下一步：填 config\groups.json，登录微信，运行 run_daily.bat。
) else (
  echo 安装失败，退出码 %CODE%。把上面的 JSON 发给技术支持。
)
pause
exit /b %CODE%
