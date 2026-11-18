@echo off
chcp 65001 >nul
REM تشغيل النظام محليًا: السيرفر + الـ Worker (عملية مستقلة) — http://127.0.0.1:8000
cd /d "%~dp0backend"
if not exist .venv (
  echo شغّل setup.bat الأول.
  pause
  exit /b 1
)
start "IT Admin Worker" /min .venv\Scripts\python.exe -m app.worker
start "" "http://127.0.0.1:8000/app.html"
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
