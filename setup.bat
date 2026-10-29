@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   微信群日报 - 安装
echo ========================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python 启动器 py。
  echo 请先安装 Python 3.12，安装时勾选 Add python.exe to PATH。
  pause
  exit /b 1
)

py -3.12 --version >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python 3.12。
  echo 请先安装 Python 3.12，安装时勾选 Add python.exe to PATH。
  pause
  exit /b 1
)

if not exist "config\groups.json" (
  echo 创建群配置 config\groups.json...
  copy "config\groups.example.json" "config\groups.json" >nul
)

if not exist ".venv\Scripts\python.exe" (
  echo 创建虚拟环境...
  py -3.12 -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo 创建虚拟环境失败。
  pause
  exit /b 1
)

echo 安装依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo 安装失败，请检查网络或 pip 源。
  pause
  exit /b 1
)

echo.
echo 检查微信版本...
powershell -NoProfile -Command "$p = Get-Process Weixin,WeChat -ErrorAction SilentlyContinue | Select-Object -First 1; if ($p) { (Get-Item $p.Path).VersionInfo.FileVersion } else { '微信未运行' }"

echo.
echo 已配置的群：
".venv\Scripts\python.exe" -c "import json; c=json.load(open('config/groups.json',encoding='utf-8')); print(str(len(c.get('groups',[])))+' 个；预期 '+str(c.get('expected_groups','-'))+' 个')"

echo.
echo 安装完成。
echo 1. 编辑 config\groups.json，填完整群名和发布人
echo 2. 登录微信，主窗口不要最小化
echo 3. 双击 run_daily.bat 试跑昨天
echo.
pause
