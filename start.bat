@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PATH=%CD%\runtime\llama;%CD%\runtime\bin;%PATH%
if not exist "runtime\python\python.exe" (
  echo [ERROR] Missing runtime\python\python.exe
  echo Please extract the complete zip, not just some files.
  pause
  exit /b 1
)
echo ============================================
echo   BiliBot Local
echo   Starting Qwen3.5-4B, please wait...
echo ============================================
"runtime\python\python.exe" "app\launch.py" %*
if errorlevel 1 pause
