@echo off
title ElevenLabs Human-Agent Pipeline
cd /d "%~dp0"
python run_elevenlabs_register.py %*
if errorlevel 1 pause
pause
