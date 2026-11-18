@echo off
chcp 65001 >nul
REM اختبار الواجهة في متصفح حقيقي (Edge/Chrome) على قاعدة بيانات مؤقتة — بياناتك مش بتتلمس
cd /d "%~dp0backend"
.venv\Scripts\python.exe tests_e2e\run_e2e.py
pause
