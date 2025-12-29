@echo off
REM Input: .run\*.pid（由 scripts\start.ps1 生成）
REM Output: 一键停止（调用 scripts\stop.ps1）
REM Pos: Windows 双击入口（变更时同步更新以上注释与所属目录 FOLDER.md）

setlocal
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"

%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
if errorlevel 1 (
  echo.
  echo Stop failed. Press any key to close.
  pause >nul
)
