#!/usr/bin/env bash
# Установка локального окружения (Linux/macOS).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== Установка локального окружения prescriptions_system ==="

if ! command -v python3.12 >/dev/null 2>&1; then
    echo "Python 3.12 не найден. Установите его (например: sudo apt install python3.12 python3.12-venv)." >&2
    exit 1
fi

python3.12 --version
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Создан .env из .env.example — заполните секреты."
fi

mkdir -p data/prescriptions/inbox/customer data/prescriptions/processed \
         data/prescriptions/storage data/uploads data/ocr_workspace data/chroma backups

.venv/bin/python -m pytest -q

echo
echo "Готово. Далее:"
echo "  1) source .venv/bin/activate"
echo "  2) ./scripts/pull_models.sh   (нужен запущенный Ollama)"
echo "  3) заполнить .env"
