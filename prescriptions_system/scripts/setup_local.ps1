$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "=== Установка локального окружения prescriptions_system ==="

$check = py -3.12 --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python 3.12 не найден. Установите: winget install -e --id Python.Python.3.12"
    exit 1
}
Write-Host "Python: $check"

if (-not (Test-Path "$root\.venv")) {
    py -3.12 -m venv .venv
}
& "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$root\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "Создан .env из .env.example — заполните секреты."
}

foreach ($d in @(
    "data\prescriptions\inbox\customer",
    "data\prescriptions\processed",
    "data\prescriptions\storage",
    "data\uploads",
    "data\ocr_workspace",
    "data\chroma",
    "backups"
)) {
    New-Item -ItemType Directory -Force -Path "$root\$d" | Out-Null
}

& "$root\.venv\Scripts\python.exe" -m pytest -q

Write-Host ""
Write-Host "Готово. Далее:"
Write-Host "  1) .\.venv\Scripts\Activate.ps1"
Write-Host "  2) .\scripts\pull_models.ps1   (нужен запущенный Ollama)"
Write-Host "  3) заполнить .env"
