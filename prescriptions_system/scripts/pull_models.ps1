param(
    [string]$LlmModel = "qwen2.5:7b-instruct",
    [string]$EmbedModel = "bge-m3"
)
$ErrorActionPreference = "Stop"

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Error "ollama не найден в PATH. Установите Ollama (https://ollama.com) и запустите его."
    exit 1
}

Write-Host "Pull LLM: $LlmModel"
ollama pull $LlmModel
Write-Host "Pull embeddings: $EmbedModel"
ollama pull $EmbedModel

Write-Host ""
Write-Host "Модели на месте:"
ollama list
