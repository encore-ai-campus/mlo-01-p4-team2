@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe run-split.py --frontend-port 14029 --backend-port 14030 --bind 127.0.0.1
