@echo off
title Lqitha - Step 1 - Download datasets
cd /d "%~dp0"
call scripts\_env.bat || exit /b 1
echo.
echo === Downloading real datasets (SNCF, Amazon Berkeley Objects, OpenStreetMap, Tunisian audio, procedures) ===
"%PY%" scripts\download_data.py %*
echo.
echo Done. Log: data\raw\download_log.txt
echo STEP1_DONE > logs\step1.done
timeout /t 15
