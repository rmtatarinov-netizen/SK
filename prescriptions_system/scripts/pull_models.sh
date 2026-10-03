#!/usr/bin/env bash
# Скачивание локальных моделей через Ollama.
set -euo pipefail

LLM_MODEL="${1:-qwen2.5:7b-instruct}"
EMBED_MODEL="${2:-bge-m3}"

if ! command -v ollama >/dev/null 2>&1; then
    echo "ollama не найден в PATH. Установите Ollama (https://ollama.com) и запустите его." >&2
    exit 1
fi

echo "Pull LLM: $LLM_MODEL"
ollama pull "$LLM_MODEL"
echo "Pull embeddings: $EMBED_MODEL"
ollama pull "$EMBED_MODEL"

echo
echo "Модели на месте:"
ollama list
