@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m ui.main %*
if errorlevel 1 (
    echo.
    echo [오류] 프로그램이 정상 종료되지 않았습니다. 위 메시지를 확인하세요.
    pause
)
