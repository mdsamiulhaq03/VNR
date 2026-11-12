@echo off
rem Builds dist\VN Reader.exe (single file, no Python needed to run it).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3.13 -m venv .venv || python -m venv .venv
)
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt || goto :fail
.venv\Scripts\python -m PyInstaller --noconfirm --clean vn_reader.spec || goto :fail
echo.
echo Built: %~dp0dist\VN Reader.exe
exit /b 0
:fail
echo Build failed.
exit /b 1
