@echo off
REM EFORT Web Monitoring - build frontend (Vite -> dist/)
cd /d "%~dp0.."
cd frontend
call npm run build
pause
