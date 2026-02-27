# Start Backend
Write-Host "Starting Warehouse Swarm Intelligence System" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""

# Save the root directory path
$ROOT_DIR = $PSScriptRoot
if ([string]::IsNullOrEmpty($ROOT_DIR)) {
    $ROOT_DIR = $PWD.Path
}

Write-Host "Starting Backend Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT_DIR'; Write-Host 'Backend server starting on http://localhost:8000' -ForegroundColor Cyan; uv run python -m backend.api.main"

Write-Host "Waiting for backend to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

Write-Host ""
Write-Host "Starting Frontend Development Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT_DIR\frontend'; Write-Host 'Frontend starting on http://localhost:3000' -ForegroundColor Cyan; bun run dev"

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "System is starting up!" -ForegroundColor Green
Write-Host ""
Write-Host "Backend:  http://localhost:8000" -ForegroundColor Cyan
Write-Host "Frontend: http://localhost:3000" -ForegroundColor Cyan
Write-Host "API Docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
