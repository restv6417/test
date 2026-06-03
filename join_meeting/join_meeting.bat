@echo off
chcp 65001 >nul
cd /d "%~dp0"
python "%~dp0join_meeting.py" %*
echo.
pause
