@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ---- 解释器发现链（全部离线可用）----
rem 1) 随包 runtime（发行版自带，无需安装 Python）
rem    2026-11-07：免安装包把运行时放在解压目录根部（app 的上一级），
rem    而这里已经 cd 进 app，所以要先找 ..\runtime；同时兼容放进 app 的老布局。
if exist "..\runtime\win-x64\python\python.exe" (
  "..\runtime\win-x64\python\python.exe" launch.py %*
  goto :done
)
if exist "runtime\win-x64\python\python.exe" (
  "runtime\win-x64\python\python.exe" launch.py %*
  goto :done
)
rem 2) py 启动器（Windows 官方安装包自带，读注册表找已装 Python）
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launch.py %*
  goto :done
)
rem 3) PATH 里的 python（真装了 Python 且加了 PATH 的情况）
where python >nul 2>nul
if not errorlevel 1 (
  python launch.py %*
  goto :done
)
echo [启动教师端] 未找到 Python 3.9+，两种解决办法：
echo   1. 使用带 runtime 的发行包，解压即用（推荐，完全离线）；
echo   2. 安装 Python 时勾选 Add to PATH 后重试。
pause
exit /b 1
:done
if errorlevel 1 pause
