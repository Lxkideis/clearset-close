@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Run SETUP.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m scripts.local_db start
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m scripts.seed_demo
if errorlevel 1 goto failed
echo Clearset Close: http://127.0.0.1:8766
".venv\Scripts\python.exe" -m uvicorn src.api:app --host 127.0.0.1 --port 8766
exit /b %errorlevel%
:failed
echo Startup failed. See the error above.
pause
exit /b 1
