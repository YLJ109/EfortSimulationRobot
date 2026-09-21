@echo off
setlocal
chcp 65001 >nul
REM =====================================================================
REM  EFORT Vision Service  (Hikrobot GigE camera + YOLO)
REM  Runs camera/camera_service.py on the interpreter that carries
REM  torch / ultralytics / the MVS SDK python wrapper.
REM
REM  Overridable via .env (project root) or real environment variables:
REM    EFORT_CAMERA_PYTHON      interpreter path
REM    EFORT_MVS_SDK_DIR        dir containing MvCameraControl_class.py
REM    EFORT_MVS_RUNTIME_DIR    MVS runtime DLL dir
REM    CAMERA_PORT              HTTP port (default 8100)
REM =====================================================================
cd /d "%~dp0.."

REM ---- load .env (KEY=VALUE, '#' comments); real env vars take precedence ----
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if not "%%~a"=="" if not defined %%~a set "%%~a=%%~b"
    )
)

set "MVCAM_RUNTIME=%EFORT_MVS_RUNTIME_DIR%"
if "%MVCAM_RUNTIME%"=="" set "MVCAM_RUNTIME=C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64"
if exist "%MVCAM_RUNTIME%" set "PATH=%MVCAM_RUNTIME%;%PATH%"

if "%CAMERA_PORT%"=="" set "CAMERA_PORT=8100"

set "CAM_PY=%EFORT_CAMERA_PYTHON%"
if "%CAM_PY%"=="" set "CAM_PY=D:\EFORT_Projects\EFORT_Camera_Python_OpenCv\venv\Scripts\python.exe"

if not exist "%CAM_PY%" (
    echo.
    echo [ERROR] vision interpreter not found:
    echo         %CAM_PY%
    echo.
    echo   Create an environment with camera\requirements.txt, install the MVS
    echo   python wrapper, then set EFORT_CAMERA_PYTHON in .env
    echo   ^(copy .env.example to .env and edit^).
    echo.
    pause
    exit /b 1
)

echo ============================================
echo  EFORT Vision Service
echo  Hikrobot GigE camera + YOLO
echo  python : %CAM_PY%
echo  url    : http://127.0.0.1:%CAMERA_PORT%
echo ============================================
echo.

"%CAM_PY%" camera\camera_service.py

if errorlevel 1 (
    echo.
    echo [ERROR] service exited with code %errorlevel%
    echo Check: camera powered on, cable on the 192.168.1.x NIC,
    echo        and camera not busy inside the MVS client.
    pause
)
