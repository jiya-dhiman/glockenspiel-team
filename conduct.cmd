@echo off
rem Start Kevin's hand-gesture conductor and send its beats to the Pi.
rem   conduct.cmd --pi 172.20.10.3 -- --demo     (synthetic beats, no camera)
rem   conduct.cmd --pi 172.20.10.3               (camera)
rem Expects mini-robotic-concert cloned next to this folder and its setup.cmd already run.
setlocal
set "CONCERT=%~dp0..\mini-robotic-concert"
if not "%CONCERT_DIR%"=="" set "CONCERT=%CONCERT_DIR%"
if not exist "%CONCERT%\.venv\Scripts\python.exe" (
    echo Can't find "%CONCERT%\.venv". Clone mini-robotic-concert next to this folder and run its setup.cmd first.
    exit /b 1
)
"%CONCERT%\.venv\Scripts\python.exe" "%~dp0conductor_bridge.py" conductor --concert-dir "%CONCERT%" %*
