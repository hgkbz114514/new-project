@echo off
rem vpnkit first-run installer. Pure ASCII by design (see vpnkit.bat).
chcp 65001 >nul
title vpnkit setup
setlocal
cd /d "%~dp0"

echo.
echo   ============================================================
echo     vpnkit  setup
echo   ============================================================
echo.

rem ---- 1. locate python -------------------------------------------------
set "PY="
for %%V in (313 312 311 310 39 38) do (
    if not defined PY (
        if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
            set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        )
    )
)
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo   [X] Python 3.8+ not found.
    echo       Get it from https://www.python.org/downloads/
    echo       Tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)
rem cmd's `for /f ... in ('"<quoted path>" -c "..."')` mangles nested quotes:
rem the parser splits on the inner quote and you get
rem   'C:\...\python.exe" -c "import' is not recognized as an internal command
rem Writing to a temp file sidesteps the quoting entirely.
rem
rem And `set /p VAR=<file` must NOT sit inside a parenthesised block: cmd binds
rem the redirection when it parses the whole block, before the file is created,
rem which fails with "The system cannot find the file specified" and leaves
rem VAR empty. So the read below is a bare single-line `if`.
set "PYVER=unknown"
"%PY%" -c "import sys;print(sys.version.split()[0])" > "%TEMP%\vpnkit_pyver.txt" 2>nul
if exist "%TEMP%\vpnkit_pyver.txt" set /p PYVER=<"%TEMP%\vpnkit_pyver.txt"
del "%TEMP%\vpnkit_pyver.txt" >nul 2>nul
echo   [1/4] Python %PYVER%  ->  %PY%

rem ---- 2. dependencies -------------------------------------------------
echo   [2/4] installing requests + pyyaml ...
"%PY%" -m pip install --quiet --disable-pip-version-check requests pyyaml
if errorlevel 1 (
    echo   [X] pip install failed. Check your network / proxy.
    echo       If you are behind the GFW, set a proxy first:
    echo         set HTTPS_PROXY=http://127.0.0.1:7897
    pause
    exit /b 1
)
echo         ok

rem ---- 3. locate the mihomo core ---------------------------------------
echo   [3/4] looking for verge-mihomo.exe ...
"%PY%" "%~dp0bin\vpn.py" doctor
if errorlevel 1 (
    echo.
    echo   [!] The check above failed. Fix whatever it flagged, then re-run.
    echo.
    echo       If the core was not found: vpnkit drives an EXISTING Clash
    echo       Verge install rather than bundling a core of its own.
    echo         Install: https://github.com/clash-verge-rev/clash-verge-rev/releases
    echo         Or point at a core you already have:
    echo           set VK_CORE=C:\path\to\verge-mihomo.exe
    echo.
    echo       If dependencies were missing, this usually means pip could not
    echo       reach PyPI. Behind the GFW, start your proxy first:
    echo         set HTTPS_PROXY=http://127.0.0.1:7897
    echo.
    pause
    exit /b 1
)

rem ---- 4. first pipeline run -------------------------------------------
echo   [4/4] first harvest + speed test (about 6 minutes) ...
echo         This pulls public sources, TCP-probes them, runs a real
echo         core to check who actually forwards traffic, then measures
echo         throughput. Safe to Ctrl-C; nothing is written to the system.
echo.
"%PY%" "%~dp0bin\vk.py" all
if errorlevel 1 (
    echo.
    echo   [!] pipeline failed. See 09_logs\ for details.
    pause
    exit /b 1
)

echo.
echo   ============================================================
echo     done. next:
echo.
echo       vpnkit.bat                ^<- menu, or
echo       python bin\vpn.py start   ^<- start + enable system proxy
echo.
echo     to keep it alive across reboots:
echo       python bin\vpn.py autostart
echo   ============================================================
echo.
pause
