@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if exist "runtime\win-x64\python\python.exe" set "PY=runtime\win-x64\python\python.exe"
"%PY%" launch.py --quickform background %*
if errorlevel 1 pause
