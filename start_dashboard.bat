@echo off
chcp 65001 >nul
title 중드 숏폼 스튜디오 - 이 창을 닫으면 꺼집니다
cd /d "%~dp0"

rem 이미 실행 중이면 브라우저만 연다
powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:8700/api/config -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    echo 이미 실행 중입니다. 브라우저를 엽니다.
    start "" http://127.0.0.1:8700
    timeout /t 3 >nul
    exit /b
)

if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv 가 없습니다. README 의 설치 방법대로 먼저 설치하세요.
    pause
    exit /b 1
)

echo ============================================================
echo  중드 숏폼 스튜디오
echo   - 이 PC:   http://127.0.0.1:8700
echo   - 외부:    https://auto-cn.hot-news.app  (비밀번호: .env 의 DASHBOARD_PASSWORD)
echo   - 끄려면 이 창을 닫으세요.
echo ============================================================

rem 서버가 뜰 시간을 두고 브라우저 열기
start "" /min cmd /c "timeout /t 5 >nul & start "" http://127.0.0.1:8700"

".venv\Scripts\python.exe" -u main.py web --remote

echo.
echo 대시보드가 종료되었습니다.
pause
