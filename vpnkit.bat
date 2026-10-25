@echo off
rem Pure ASCII on purpose: cmd.exe parses .bat with the OEM codepage and can
rem desync on multi-byte text, breaking even lines it never had to echo.
rem All UI text lives in bin\menu.py.
chcp 65001 >nul
title vpnkit
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

rem Find a usable Python 3.8+ without assuming where it was installed.
set "PY="
for %%V in (313 312 311 310 39 38) do (
    if not defined PY (
        if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
            set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        )
    )
)
if not defined PY (
    if exist "%ProgramFiles%\Python313\python.exe" set "PY=%ProgramFiles%\Python313\python.exe"
)
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo   Python 3.8+ not found.
    echo   Install it from https://www.python.org/downloads/
    echo   Remember to tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

"%PY%" "%~dp0bin\menu.py"
if errorlevel 1 pause
