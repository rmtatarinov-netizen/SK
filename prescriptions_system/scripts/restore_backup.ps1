# Restore from snapshot (Step 12). Usage: .\restore_backup.ps1 backups\backup_... [-Force]
param([Parameter(Mandatory = $true)][string]$BackupDir, [switch]$Force)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "venv not found: run scripts\setup_local.ps1 first"; exit 1 }
if ($Force) { & $py scripts\restore_backup.py $BackupDir --force } else { & $py scripts\restore_backup.py $BackupDir }
exit $LASTEXITCODE
