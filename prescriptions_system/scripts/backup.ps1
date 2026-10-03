# Backup snapshot (Step 12). Calls scripts/backup.py with the project venv.
param([int]$Keep = 30, [string]$Dest = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "venv not found: run scripts\setup_local.ps1 first"; exit 1 }
if ($Dest) { & $py scripts\backup.py --keep $Keep --dest $Dest } else { & $py scripts\backup.py --keep $Keep }
exit $LASTEXITCODE
