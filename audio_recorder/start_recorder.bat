@echo off
chcp 65001 >nul
cd /d "%~dp0"
python "%~dp0audio_recorder.py" %*
echo.
pause
