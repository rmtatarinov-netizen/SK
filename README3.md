# Система автоматизации работы с предписаниями строительного контроля (prescriptions_system)

Данная система предназначена для автоматизации жизненного цикла предписаний строительного контроля: от загрузки скан-копий и автоматического извлечения данных с помощью LLM до контроля исполнения и уведомления ответственных лиц через Telegram.

## 1. Системные требования

- **Python 3.12**: Строго эта версия.
- **Ollama**: Локальный движок для запуска LLM и эмбеддингов.
  - Модели для загрузки: `qwen2.5:7b-instruct` (анализ текста) и `bge-m3` (эмбеддинги для RAG).
- **Git**: Инструмент управления версиями.

## 2. Установка и развертывание

В системе реализовано разделение на два виртуальных окружения для решения конфликтов зависимостей (в частности, библиотеки `protobuf`), необходимых для работы OCR и RAG.

### Шаг 1: Создание окружений и установка зависимостей

Запустите следующие команды в корне проекта (PowerShell):

```powershell
# Создание изолированных окружений
python -m venv .venv-ocr
python -m venv .venv-rag

# Установка зависимостей для OCR (Распознавание)
.\.venv-ocr\Scripts\pip install -r prescriptions_system\requirements.txt
.\.venv-ocr\Scripts\pip install -r prescriptions_system\requirements-ocr.txt
.\.venv-ocr\Scripts\pip install "protobuf<=3.20.2"

# Установка зависимостей для RAG (База знаний и Web)
.\.venv-rag\Scripts\pip install -r prescriptions_system\requirements.txt
.\.venv-rag\Scripts\pip install -r prescriptions_system\requirements-rag.txt
.\.venv-rag\Scripts\pip install "protobuf>=4.25.8"
```

### Шаг 2: Загрузка моделей Ollama

Убедитесь, что Ollama запущена, и выполните:
```powershell
ollama pull qwen2.5:7b-instruct
ollama pull bge-m3
```

### Шаг 3: Первичная настройка

```powershell
# Создание первого администратора
.\.venv-rag\Scripts\python.exe prescriptions_system\scripts\create_first_admin.py
```

---

## 3. Эксплуатация системы

### Работа с нормативной базой (RAG)
Для того чтобы система могла предлагать ссылки на ГОСТ, СНиП и другие нормы:
1. Поместите PDF-файлы с нормативными документами в папку `docs/priors/`.
2. Запустите процесс индексации через окружение RAG:
   ```powershell
   .\.venv-rag\Scripts\python.exe prescriptions_system\scripts\load_norms.py docs\priors\ --pattern "*.pdf"
   ```

### Запуск основных компонентов

| Компонент | Окружение | Команда запуска | Описание |
| :--- | :--- | :--- | :--- |
| **Web-интерфейс** | `.venv-rag` | `.\.venv-rag\Scripts\python.exe prescriptions_system\scripts\run_web.py` | Панель управления (http://127.0.0.1:8000) |
| **OCR-пайплайн** | `.venv-ocr` | `.\.venv-ocr\Scripts\python.exe prescriptions_system\scripts\run_ocr_pipeline.py` | Мониторинг папки inbox и распознавание |
| **Уведомления** | `.venv-rag` | `.\.venv-rag\Scripts\python.exe prescriptions_system\scripts\run_notification_worker.py` | Доставка сообщений в Telegram |
| **Telegram-бот** | `.venv-rag` | `.\.venv-rag\Scripts\python.exe prescriptions_system\scripts\run_bot.py` | Интерфейс для подрядчиков |

---

## 4. Структура данных

- `docs/priors/` — исходные PDF-файлы нормативных документов.
- `data/prescriptions/inbox/customer/` — сюда следует класть сканы новых предписаний.
- `data/chroma/` — векторная база знаний.
- `data/prescriptions.db` — основная база данных SQLite.

## 5. Обслуживание

**Резервное копирование:**
```powershell
.\.venv-rag\Scripts\python.exe prescriptions_system\scripts\backup.ps1 -Keep 30
```

---

## 6. Реестр предписаний и справочники

В систему внедрена расширенная структура реестра для детального учета и классификации замечаний.

### Новые поля реестра:
- **Обоснование**: Текстовое описание причин выдачи предписания.
- **Дата выдачи** и **Дата фактического устранения**: Для контроля фактических сроков работ.
- **Управление строительством** и **Выдал предписания**: Ссылки на ответственных лиц/подразделения.
- **Категория замечания**: Классификация по степени критичности (А, В, С).
- **Вид предписания**: Типизация работ (АКЗ/ОГЗ, Ведение ИД, Геодезия, Монолит, Сварка и др.).

### Справочники и ручной ввод:
Для обеспечения единообразия данных используются системные справочники. Система поддерживает **гибридный ввод**:
1. **Выбор из списка**: Пользователь выбирает значение из предустановленного справочника.
2. **Ручной ввод**: При вводе нового значения, которого нет в списке, система автоматически создает новую запись в соответствующем справочнике.

### Наполнение справочников:
Для первичного заполнения базовых категорий и видов работ запустите:
```powershell
.\.venv-rag\Scripts\python.exe prescriptions_system\scripts\seed_references.py
```

