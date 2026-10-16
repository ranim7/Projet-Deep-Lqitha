@echo off
title Lqitha - Step 3 - Start the application
cd /d "%~dp0"
call scripts\_env.bat || exit /b 1
docker info >nul 2>&1 || (start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" & timeout /t 40 >nul)
docker compose up -d
call scripts\start_api.cmd
timeout /t 10 >nul
start "" http://localhost:8000
echo.
echo  Web app : http://localhost:8000
echo  n8n     : http://localhost:5678   (first visit: create your local owner account)
echo  API docs: http://localhost:8000/docs
timeout /t 10
