@echo off
title Lqitha - Step 2 - Setup (models, data, knowledge base, n8n)
cd /d "%~dp0"
call scripts\_env.bat || exit /b 1
echo. > logs\setup.log

echo [1/7] Pulling the free local models (Ollama bge-m3 ~1.2 GB + Gemma 3 4B fallback ~3.3 GB, first time only)...
ollama pull bge-m3 >> logs\setup.log 2>&1
ollama pull gemma3:4b >> logs\setup.log 2>&1

echo [2/7] Checking the free Groq API key and models...
"%PY%" scripts\check_groq.py >> logs\setup.log 2>&1

echo [3/7] Downloading any missing dataset...
"%PY%" scripts\download_data.py >> logs\setup.log 2>&1

echo [4/7] Preparing data (places, items, Delhi Metro, TSA)...
"%PY%" scripts\prepare_data.py >> logs\setup.log 2>&1

echo [5/7] Building the RAG knowledge base (2 chunking strategies, bge-m3, NumPy vector store)...
"%PY%" scripts\build_kb.py >> logs\setup.log 2>&1

echo [6/7] Generating the n8n workflows from the prompt library + seeding the demo database...
"%PY%" scripts\build_n8n.py >> logs\setup.log 2>&1
"%PY%" scripts\seed_demo.py >> logs\setup.log 2>&1

echo [7/7] Starting n8n (Docker) and importing the workflows...
docker info >nul 2>&1
if errorlevel 1 (
  echo     Docker Desktop is not running - starting it...
  start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  for /l %%i in (1,1,40) do (
    timeout /t 5 >nul
    docker info >nul 2>&1 && goto :docker_ok
  )
)
:docker_ok
docker compose up -d >> logs\setup.log 2>&1
timeout /t 20 >nul
docker compose exec -T n8n n8n import:workflow --separate --input=/workflows >> logs\setup.log 2>&1
docker compose exec -T n8n n8n publish:workflow --id=LqithaMainFlow01 >> logs\setup.log 2>&1
docker compose exec -T n8n n8n publish:workflow --id=LqithaRematch001 >> logs\setup.log 2>&1
docker compose restart n8n >> logs\setup.log 2>&1
echo SETUP_DONE > logs\step2.done
echo.
echo Setup finished. Details: logs\setup.log
timeout /t 15
