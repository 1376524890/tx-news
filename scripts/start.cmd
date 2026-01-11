@echo off
REM Input: Windows + PowerShell + Docker Desktop(Compose)
REM Output: 一键启动（调用 scripts\start.ps1；启动 docker compose profile=app）
REM Pos: Windows 双击入口（变更时同步更新以上注释与所属目录 FOLDER.md）

setlocal
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"

%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 (
  echo.
  echo Startup failed. Press any key to close.
  pause >nul
)
