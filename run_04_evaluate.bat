@echo off
title Lqitha - Step 4 - Evaluations
cd /d "%~dp0"
call scripts\_env.bat || exit /b 1
echo Results go to reports\results\ (JSON + Markdown). LLM calls are cached: re-running is fast and free.
echo [1/5] Retrieval (RAG) evaluation...
"%PY%" scripts\eval_rag.py > logs\eval_rag.log 2>&1
echo [2/5] Speech-to-text evaluation (Tunisian Derja)...
"%PY%" scripts\eval_stt.py > logs\eval_stt.log 2>&1
echo [3/5] Prompt evaluation v1 vs v2 (several minutes, free-tier rate limits)...
"%PY%" scripts\eval_prompts.py > logs\eval_prompts.log 2>&1
echo [4/5] Matching + verifier evaluation...
"%PY%" scripts\eval_matching.py > logs\eval_matching.log 2>&1
echo [5/5] Seeding the demo database...
"%PY%" scripts\seed_demo.py > logs\seed_demo.log 2>&1
echo Done.
timeout /t 15
