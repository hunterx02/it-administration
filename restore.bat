@echo off
chcp 65001 >nul
REM استعادة نسخة احتياطية — اقفل نافذة السيرفر ونافذة الـ Worker الأول
cd /d "%~dp0backend"
.venv\Scripts\python.exe -m app.cli restore %1
pause
