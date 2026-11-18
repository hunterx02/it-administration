@echo off
chcp 65001 >nul
REM تجهيز أول مرة: بيئة Python + المكتبات + قاعدة البيانات + أول حساب Super Admin
cd /d "%~dp0backend"
if not exist .venv (
  echo [1/3] انشاء بيئة Python...
  python -m venv .venv || goto :err
)
echo [2/3] تثبيت المكتبات...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt || goto :err
echo [3/3] تجهيز قاعدة البيانات وانشاء حساب المدير...
.venv\Scripts\python.exe -m app.cli create-admin
echo.
echo خلاص. شغّل run.bat عشان تفتح النظام.
pause
exit /b 0
:err
echo حصل خطأ في التجهيز.
pause
exit /b 1
