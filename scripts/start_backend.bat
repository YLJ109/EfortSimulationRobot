@echo off
chcp 65001 >nul
REM EFORT Web Monitoring - backend (FastAPI + WebSocket + SQLite)
cd /d "%~dp0.."
cd backend
call .venv\Scripts\activate.bat
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
