@echo off
rem vpnkit one-click installer - AI agents and humans: run this after cloning or standalone.
rem Windows only. No admin required. Idempotent (safe to re-run).
setlocal EnableExtensions
chcp 65001 >nul
title vpnkit installer

cd /d "%~dp0"

echo.
echo  ============================================
echo    vpnkit one-click installer
echo    GitHub: https://github.com/hgkbz114514/vpnkit
echo  ============================================
echo.

rem --- 1. find python -------------------------------------------------------
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  set "P313=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  if exist "%P313%" set "PY=%P313%"
)
if not defined PY (
  echo  [X] Python 3.9+ not found.
  echo      AI agent: install with  winget install -e --id Python.Python.3.13
  echo      Human:    download from https://www.python.org/downloads/
  echo      then re-run this script.
  exit /b 1
)
echo  [1/4] Python OK

rem --- 2. deps ---------------------------------------------------------------
echo  [2/4] Installing dependencies (requests, pyyaml) ...
%PY% -m pip install --quiet --disable-pip-version-check requests pyyaml
if errorlevel 1 (
  echo      pip failed. If PyPI is blocked, set proxy first:
  echo        set HTTPS_PROXY=http://127.0.0.1:7897
  exit /b 1
)
echo      deps OK

rem --- 3. environment check --------------------------------------------------
echo  [3/4] Environment check ...
%PY% bin\vkcore.py
%PY% bin\vpn.py doctor
if errorlevel 1 (
  echo  [X] doctor failed - read the lines above.
  echo      AI agent: fix the marked problems, then re-run install.bat.
  exit /b 1
)

rem --- 4. first build ---------------------------------------------------------
if exist "profiles\vpnkit.yaml" (
  echo  [4/4] Config already exists - skipping node collection.
  echo        To rebuild:  python bin\vk.py all
) else (
  echo  [4/4] Collecting and testing free nodes (about 6 minutes) ...
  %PY% bin\vk.py all
  if errorlevel 1 (
    echo  [X] node collection failed - see output above.
    exit /b 1
  )
)

echo.
%PY% bin\vpn.py start
echo.
echo  ============================================
echo    DONE. Proxy is running on 127.0.0.1:7897
echo    System proxy has been enabled.
echo.
echo    Menu console:  double-click vpnkit.bat
echo    Status:        python bin\vpn.py status
echo    Autostart:     python bin\vpn.py autostart install
echo  ============================================
pause
