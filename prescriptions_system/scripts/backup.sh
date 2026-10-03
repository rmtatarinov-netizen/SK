#!/usr/bin/env bash
# Backup snapshot (Step 12). Usage: ./scripts/backup.sh [keep]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=".venv/bin/python"
[ -x "$PY" ] || { echo "venv not found: run ./scripts/setup_local.sh" >&2; exit 1; }
"$PY" scripts/backup.py --keep "${1:-30}" "${@:2}"
