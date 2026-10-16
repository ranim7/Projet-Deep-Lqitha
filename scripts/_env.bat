@echo off
REM Shared helper: finds a Python, creates the project virtual env (.venv) once,
REM and sets PY to the venv interpreter. Called by every run_*.bat file.
cd /d "%~dp0\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

if exist ".venv\Scripts\python.exe" goto :venv_ok

set BASEPY=
if exist "%USERPROFILE%\anaconda3\python.exe" set BASEPY=%USERPROFILE%\anaconda3\python.exe
if "%BASEPY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set BASEPY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
if "%BASEPY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set BASEPY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if "%BASEPY%"=="" for /f "delims=" %%i in ('where python 2^>nul ^| findstr /v WindowsApps') do if "%BASEPY%"=="" set BASEPY=%%i
if "%BASEPY%"=="" (
  echo [ERROR] No Python found. Install Python 3.10+ or Anaconda. > logs\env_error.txt
  echo No Python found. Install Python 3.10+ or Anaconda.
  pause
  exit /b 1
)
echo Creating virtual environment with %BASEPY% ...
"%BASEPY%" -m venv .venv >> logs\env_setup.log 2>&1

:venv_ok
set PY=%CD%\.venv\Scripts\python.exe
"%PY%" -m pip install -q --upgrade pip >> logs\env_setup.log 2>&1
"%PY%" -m pip install -q -r requirements.txt >> logs\env_setup.log 2>&1
echo python=%PY% > logs\env.txt
"%PY%" --version >> logs\env.txt 2>&1
where docker >> logs\env.txt 2>&1
docker version --format "docker server {{.Server.Version}}" >> logs\env.txt 2>&1
where ollama >> logs\env.txt 2>&1
ollama list >> logs\env.txt 2>&1
exit /b 0
