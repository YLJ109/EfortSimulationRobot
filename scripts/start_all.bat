@echo off
REM =====================================================================
REM  EFORT Web Monitoring - start EVERYTHING
REM    clean old services + build frontend if missing
REM    + vision service :8100 + backend :8000 + open browser
REM  Thin wrapper around the root launcher (default action = all).
REM =====================================================================
call "%~dp0..\run.bat" all
