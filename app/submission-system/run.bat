@echo off
REM 学生提交系统 · Windows 启动器
cd /d "%~dp0"
python server.py %*
if errorlevel 1 (
  echo 启动失败，请确认已安装 Python 3。
  pause
)
