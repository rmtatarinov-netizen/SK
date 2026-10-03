#!/usr/bin/env bash
# Restore from snapshot (Step 12). Usage: ./scripts/restore_backup.sh <backup_dir> [--force]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=".venv/bin/python"
[ -x "$PY" ] || { echo "venv not found: run ./scripts/setup_local.sh" >&2; exit 1; }
[ $# -ge 1 ] || { echo "usage: $0 <backup_dir> [--force]" >&2; exit 2; }
"$PY" scripts/restore_backup.py "$@"
