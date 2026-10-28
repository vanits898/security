@echo off
taskkill /f /im python.exe
taskkill /f /im ngrok.exe
timeout /t 3
cd /d "%~dp0"
start python All4_2.py