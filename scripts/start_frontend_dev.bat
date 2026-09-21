@echo off
REM EFORT Web Monitoring - frontend dev server (Vite, port 5173)
cd /d "%~dp0.."
cd frontend
call npm run dev
pause
