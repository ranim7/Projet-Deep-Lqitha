@echo off
REM Starts the Lqitha API in its own minimised window, WITHOUT inheriting the caller's open files.
cd /d "%~dp0\.."
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
taskkill /FI "WINDOWTITLE eq Lqitha API*" /T /F >nul 2>&1
set APILOG=logs\api_%RANDOM%.log
echo %APILOG%> logs\api_last.txt
powershell -NoProfile -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/k title Lqitha API (keep open) && .venv\Scripts\python.exe -X faulthandler -m uvicorn lqitha.api:app --host 0.0.0.0 --port 8000 > %APILOG% 2>&1' -WorkingDirectory '%CD%' -WindowStyle Minimized"
