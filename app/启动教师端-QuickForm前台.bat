@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if exist "runtime\win-x64\python\python.exe" set "PY=runtime\win-x64\python\python.exe"
echo 前台模式：QuickForm 的日志会直接显示在本窗口，关闭窗口即同时停止工作台与 QuickForm。
echo.
"%PY%" launch.py --quickform foreground %*
if errorlevel 1 pause
