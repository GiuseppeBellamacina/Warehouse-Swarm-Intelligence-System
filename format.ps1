#!/usr/bin/env pwsh
# Format and lint all code with Isort, Black and Ruff

Write-Host "================================" -ForegroundColor Cyan
Write-Host "  Code Formatting & Linting" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Run Isort
Write-Host "📦 Running Isort..." -ForegroundColor Yellow
isort .
$isortExit = $LASTEXITCODE

if ($isortExit -eq 0) {
    Write-Host "✅ Isort completed successfully" -ForegroundColor Green
} else {
    Write-Host "❌ Isort failed with exit code $isortExit" -ForegroundColor Red
}

Write-Host ""

# Run Black
Write-Host "🎨 Running Black formatter..." -ForegroundColor Yellow
black .
$blackExit = $LASTEXITCODE

if ($blackExit -eq 0) {
    Write-Host "✅ Black formatting completed successfully" -ForegroundColor Green
} else {
    Write-Host "❌ Black formatting failed with exit code $blackExit" -ForegroundColor Red
}

Write-Host ""

# Run Ruff
Write-Host "🔍 Running Ruff linter with auto-fix..." -ForegroundColor Yellow
ruff check --fix .
$ruffExit = $LASTEXITCODE

if ($ruffExit -eq 0) {
    Write-Host "✅ Ruff linting completed successfully" -ForegroundColor Green
} else {
    Write-Host "⚠️  Ruff found issues (exit code $ruffExit)" -ForegroundColor Yellow
}

# npm linting
Write-Host "📦 Running npm linting in frontend/..." -ForegroundColor Yellow
Push-Location "frontend"
npm run lint
$npmExit = $LASTEXITCODE
Pop-Location

if ($npmExit -eq 0) {
    Write-Host "✅ npm linting completed successfully" -ForegroundColor Green
} else {
    Write-Host "⚠️  npm linting found issues (exit code $npmExit)" -ForegroundColor Yellow
}

# npm tsc
Write-Host "📦 Running npm TypeScript compilation in frontend/..." -ForegroundColor Yellow
Push-Location "frontend"
npx tsc --noEmit
$tscExit = $LASTEXITCODE
Pop-Location

if ($tscExit -eq 0) {
    Write-Host "✅ npm TypeScript compilation completed successfully" -ForegroundColor Green
} else {
    Write-Host "⚠️  npm TypeScript compilation found issues (exit code $tscExit)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "  Formatting Complete!" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan