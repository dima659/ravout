@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python -m pip install -r requirements.lock.txt
if errorlevel 1 goto failed
set DEV_MODE=true
start http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto end
:failed
echo Setup failed. Install Python 3.12+ and try again.
pause
:end
