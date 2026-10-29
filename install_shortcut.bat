@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\群日报.lnk'); $s.TargetPath='%~dp0open_daily.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\shell32.dll,70'; $s.Description='打开最近一份微信群日报'; $s.Save()"

if errorlevel 1 (
  echo 创建快捷方式失败。
) else (
  echo 已在桌面创建“群日报”快捷方式。
)
pause
