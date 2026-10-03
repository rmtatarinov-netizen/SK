# prescriptions_system

Система автоматизации работы с предписаниями строительного контроля.
Спецификация: `../Prompt_v2.4.md` (реализация поэтапная, Шаги 1–14).

**Стек:** Python 3.12, FastAPI + Jinja2, SQLAlchemy 2.x + Alembic, SQLite (WAL),
PaddleOCR (ru) + PyMuPDF, Ollama `qwen2.5:7b-instruct` + `bge-m3`, ChromaDB,
Telegram-бот, pytest. Всё локально, без обязательных внешних облаков.

## Требования

- Python 3.12 (`winget install -e --id Python.Python.3.12`)
- Ollama (`https://ollama.com`) для LLM и эмбеддингов
- Git, при необходимости ffmpeg/системные библиотеки для OCR — на Шаге 5

## Быстрый старт (Windows)

```powershell
cd prescriptions_system
.\scripts\setup_local.ps1        # venv + базовые зависимости + каталоги data/
.\.venv\Scripts\Activate.ps1
.\scripts\pull_models.ps1       # ollama pull qwen2.5:7b-instruct, bge-m3
Copy-Item .env.example .env      # заполнить секреты
python scripts\create_first_admin.py   # первый администратор (Шаг 3)
python -m pytest                 # тесты конфигурации и схемы
```

> PaddleOCR (шахматные сканы) ставится отдельно: `pip install -r requirements-ocr.txt`
> (~600 МБ, paddlepaddle CPU). Текстовые PDF обрабатываются через PyMuPDF без него;
> без установленного движка файлы-сканы остаются в inbox с понятной ошибкой.

Linux/macOS: `./scripts/setup_local.sh`, `./scripts/pull_models.sh`.

Дополнительные группы зависимостей ставятся на своих шагах:

| Файл | Шаг | Содержимое |
|---|---|---|
| `requirements.txt` | 1 | ядро: web, БД, конфиг, auth, PDF-текст |
| `requirements-ocr.txt` | 5 | PaddlePaddle + PaddleOCR |
| `requirements-rag.txt` | 7 | ChromaDB |
| `requirements-reports.txt` | 11 | WeasyPrint, openpyxl |

## Конфигурация

- `config.yaml` — параметры (пути к данным, Ollama, RAG, retry-политика уведомлений). Пути относительные, разрешаются от корня проекта.
- `.env` — секреты (`WEB_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `MAX_API_*`, `FIRST_ADMIN_*`). Не коммитить.
- Загрузка: `src/config.py` → `get_settings()` (Pydantic Settings, YAML + env).

## Структура

```text
src/
  config.py           загрузка конфигурации
  db/                 модели, engine, репозитории (Шаг 2)
  users/              пользователи и роли (Шаг 3)
  prescriptions/      статусная машина, печать, сканы (Шаг 4)
  ocr/                пайплайн распознавания (Шаг 5)
  llm/                извлечение полей и норм (Шаг 6)
  rag/                нормативная база, рекомендации (Шаг 7)
  notifications/      outbox, адаптеры Telegram/MAX (Шаг 8)
  bots/               Telegram/MAX боты (Шаг 10/14)
  web/                FastAPI + Jinja2 (Шаг 9)
  reports/            отчёты и дашборды (Шаг 11)
data/                 inbox / processed / storage / uploads (не коммитится)
docs/                 схема БД, надёжность, контракт MAX
scripts/              установка, модели, пайплайны, бэкапы
tests/                pytest (внешние сервисы — фейки, сеть не нужна)
```

## Безопасные значения по умолчанию (Приложение A)

До получения ответов на уточняющие вопросы: OCR регистрируется только после
проверки инспектором; получатели — ответственный Подрядчика и назначенный
Инспектор; канал — Telegram (MAX недоступен, заглушка); возврат — только
`fixed_pending_review → in_work` с комментарием; подрядчик меняет только статус
и вложения; одно предписание = один документ с пунктами; исходные документы не
удаляются никогда.

## Порядок разработки

Шаги выполняются последовательно по спецификации; каждый следующий — только
после проверки предыдущего. Эксплуатационный запуск — после Шага 13 (тесты и
критерии готовности MVP).
