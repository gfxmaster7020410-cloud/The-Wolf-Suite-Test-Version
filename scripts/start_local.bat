@echo off
REM Project Wolf — local launch (API + Control Center)
set ROOT=%~dp0..
cd /d "%ROOT%"

echo Starting Unified API on :8080 ...
start "Wolf API" cmd /k "uvicorn main:app --host 127.0.0.1 --port 8080"

timeout /t 2 /nobreak >nul

echo Starting Control Center on :3000 ...
cd /d "%ROOT%\apps\control_center"
start "Wolf Control Center" cmd /k "npm run dev"

echo.
echo API:            http://127.0.0.1:8080/health
echo Control Center: http://127.0.0.1:3000
echo Docs:           http://127.0.0.1:8080/docs
echo.
pause
