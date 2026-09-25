@echo off
setlocal
chcp 65001 >nul
title EFORT Web Monitoring - setup
cd /d "%~dp0"

REM =====================================================================
REM  EFORT Web Monitoring - one-click setup
REM
REM  Does everything needed for a fresh machine:
REM    1) locate python + node
REM    2) create backend\.venv and install backend requirements
REM    3) install frontend npm dependencies
REM    4) create runtime directories (data / logs / camera captures)
REM    5) build the frontend into frontend\dist
REM    6) create .env from .env.example if missing
REM
REM  Safe to re-run: every step is idempotent.
REM  ASCII only on purpose (cmd parses .bat as GBK on Chinese Windows).
REM =====================================================================

echo ============================================================
echo   EFORT Web Monitoring - setup
echo ============================================================
echo.

set "FAILED=0"

REM ---------------- 1. locate python ----------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
    echo [FAIL] Python not found on PATH.
    echo        Install Python 3.10+ from https://www.python.org/downloads/
    echo        and tick "Add python.exe to PATH" during install.
    goto FAIL
)
set "PYMAJ="
set "PYMIN="
for /f "tokens=1,2" %%a in ('%PY% -c "import sys;print(sys.version_info[0],sys.version_info[1])" 2^>nul') do (
    set "PYMAJ=%%a"
    set "PYMIN=%%b"
)
if not defined PYMAJ (
    echo [FAIL] Could not query the Python version via "%PY%".
    goto FAIL
)
if %PYMAJ% LSS 3 (
    echo [FAIL] Python %PYMAJ%.%PYMIN% is too old - need 3.10 or newer.
    goto FAIL
)
if %PYMAJ% EQU 3 if %PYMIN% LSS 10 (
    echo [FAIL] Python %PYMAJ%.%PYMIN% is too old - need 3.10 or newer.
    goto FAIL
)
echo [OK]   Python %PYMAJ%.%PYMIN%   ^(%PY%^)

REM ---------------- 1b. locate node ----------------
where node >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Node.js not found on PATH.
    echo        Install Node 20+ from https://nodejs.org/  ^(LTS^).
    goto FAIL
)
set "NODEVER="
for /f "delims=" %%v in ('node -v 2^>nul') do set "NODEVER=%%v"
echo [OK]   Node %NODEVER%

REM ---------------- 2. backend venv ----------------
echo.
if exist "backend\.venv\Scripts\python.exe" (
    echo [2/6] backend venv already present - skipping creation
) else (
    echo [2/6] creating backend venv ^(backend\.venv^) ...
    %PY% -m venv "backend\.venv"
    if errorlevel 1 (
        echo [FAIL] venv creation failed.
        goto FAIL
    )
)

echo [3/6] installing backend dependencies ...
"backend\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
"backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements.txt --disable-pip-version-check
if errorlevel 1 (
    echo [FAIL] pip install failed. Check your network / proxy settings.
    goto FAIL
)

REM ---------------- 3. frontend deps ----------------
echo.
echo [4/6] installing frontend dependencies ...
pushd frontend
if exist "package-lock.json" (
    call npm ci
) else (
    call npm install
)
if errorlevel 1 set "FAILED=1"
popd
if %FAILED% NEQ 0 (
    echo [FAIL] npm install failed. Check your network / proxy settings.
    goto FAIL
)

REM ---------------- 4. runtime dirs ----------------
echo.
echo [5/6] creating runtime directories ...
if not exist "data"            mkdir "data"
if not exist "logs"            mkdir "logs"
if not exist "camera\captures" mkdir "camera\captures"
echo       data\  logs\  camera\captures\

REM ---------------- 5. build frontend ----------------
echo.
echo [6/6] building frontend ...
pushd frontend
call npm run build
if errorlevel 1 set "FAILED=1"
popd
if %FAILED% NEQ 0 (
    echo [WARN] frontend build failed - run.bat will try again on next launch.
) else (
    echo       built -^> frontend\dist
)

REM ---------------- 6. .env template ----------------
if not exist ".env" if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo.
    echo       created .env from .env.example ^(all defaults, edit as needed^)
)

echo.
echo ============================================================
echo   Setup complete.
echo ============================================================
echo.
echo   Next step  :  double-click  run.bat
echo   Browser    :  http://localhost:8000
echo.
echo   Optional   :  copy .env.example to .env to override
echo                 EFORT_SIMULATE / EFORT_DB_URL / CAMERA_PORT etc.
echo.
pause
exit /b 0

:FAIL
echo.
echo ============================================================
echo   Setup FAILED - fix the problem above, then re-run setup.bat
echo ============================================================
pause
exit /b 1
