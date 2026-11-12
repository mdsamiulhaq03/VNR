@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo First run: setting up...
  py -3.13 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe main.py
