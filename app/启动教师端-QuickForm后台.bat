@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ---- 解释器发现链（全部离线可用），同 启动教师端.bat ----
if exist "runtime\win-x64\python\python.exe" (
  "runtime\win-x64\python\python.exe" launch.py --quickform background %*
  goto :done
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launch.py --quickform background %*
  goto :done
)
where python >nul 2>nul
if not errorlevel 1 (
  python launch.py --quickform background %*
  goto :done
)
echo [启动教师端] 未找到 Python 3.9+，两种解决办法：
echo   1. 使用带 runtime 的发行包，解压即用（推荐，完全离线）；
echo   2. 安装 Python 时勾选 Add to PATH 后重试。
pause
exit /b 1
:done
if errorlevel 1 pause
