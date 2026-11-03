@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ---- 解释器发现链（全部离线可用），同 启动教师端.bat ----
if exist "runtime\win-x64\python\python.exe" (
  "runtime\win-x64\python\python.exe" launch.py --qf-status %*
  goto :done
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launch.py --qf-status %*
  goto :done
)
where python >nul 2>nul
if not errorlevel 1 (
  python launch.py --qf-status %*
  goto :done
)
echo [QuickForm 服务状态] 未找到 Python 3.9+，请使用带 runtime 的发行包或安装 Python。
echo.
pause
exit /b 1
:done
echo.
pause
