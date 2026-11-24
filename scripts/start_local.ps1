# Project Wolf — local launch helper (PowerShell)
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Starting Unified API on :8080 ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; uvicorn main:app --host 127.0.0.1 --port 8080"

Start-Sleep -Seconds 2

Write-Host "Starting Control Center on :3000 ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root\apps\control_center'; npm run dev"

Write-Host ""
Write-Host "API:            http://127.0.0.1:8080/health" -ForegroundColor Green
Write-Host "Control Center: http://127.0.0.1:3000" -ForegroundColor Green
Write-Host "API Docs:       http://127.0.0.1:8080/docs" -ForegroundColor Green
