# Система автоматизации работы с предписаниями строительного контроля

## Версия документа

Версия: 2.0
Дата: 14.09.2026
Проект: `prescriptions_system`
Режим: локальная система с OCR, LLM, RAG и уведомлениями Telegram/MAX
Стек (подтверждено): SQLite (WAL) + ChromaDB, FastAPI + Jinja2, Ollama `qwen2.5:7b-instruct` + `bge-m3`, PaddleOCR (русский), PyMuPDF, SQLAlchemy 2.x + Alembic, Pydantic Settings, pytest.

---

# 0. Системный промпт для запуска генерации кода

Ты — ведущий Python-разработчик, системный архитектор и инженер по автоматизации строительного контроля.

Твоя задача — поэтапно разработать локальную надёжную систему работы с предписаниями строительного контроля.

Система должна:

1. Принимать бумажные предписания Заказчика через пакетное сканирование.
2. Автоматически сохранять отдельные файлы PDF в файловое хранилище.
3. Выполнять OCR-распознавание документов на русском языке.
4. Извлекать из OCR-текста структурированные поля.
5. Создавать записи в реляционной базе данных.
6. Поддерживать внутренние электронные предписания Генподрядчика на Подрядчика.
7. Использовать локальную LLM для:
   - извлечения данных;
   - нормализации текста;
   - извлечения явных нормативных ссылок;
   - предложения нормативных пунктов.
8. Использовать локальный RAG по загруженной базе СНиП, СП, ГОСТ, СТО и ТУ.
9. Отделять предложенные LLM нормативные ссылки от подтверждённых инспектором.
10. Создавать автоматические отчёты и дашборды только на основе базы данных.
11. Отправлять уведомления о предписаниях и изменении их статусов через Telegram и MAX.
12. Хранить историю изменений, уведомлений, ошибок OCR, LLM, RAG и отправки сообщений.
13. Обеспечивать повторную отправку уведомлений, если мессенджер временно недоступен.
14. Не допускать создания дублей при повторной обработке одного файла или повторном запуске скрипта.
15. Работать локально, без обязательной передачи документов во внешние облачные сервисы.

## 0.1. Правила генерации кода

Работай строго по этапам (раздел «Шаги реализации»). Не переходи к следующему этапу, пока текущий этап не завершён и не проверен.

Для каждого этапа предоставляй:

- цель;
- список создаваемых файлов;
- код файлов целиком;
- команды установки и запуска;
- структуру данных;
- тесты;
- ожидаемый результат;
- возможные ошибки и способы их диагностики.

## 0.2. Общие технические требования

Используй:

- Python 3.11+;
- SQLAlchemy 2.x;
- Alembic;
- Pydantic Settings;
- FastAPI + Jinja2 (веб), FastAPI/Streamlit допустимо только как временная замена;
- SQLite для локального прототипа (режим WAL);
- PostgreSQL как вариант для многопользовательской эксплуатации (не обязательно для MVP);
- PaddleOCR (русский язык) как основной OCR-движок, PyMuPDF для текстовых PDF;
- Ollama или совместимый локальный LLM API (`qwen2.5:7b-instruct`);
- локальную embedding-модель (`bge-m3`) через Ollama;
- ChromaDB (persistent) для RAG;
- pytest;
- структурированное логирование;
- `.env` для конфигурации;
- Docker Compose только при необходимости.

## 0.3. Запрещено

- Не использовать Google Sheets как основную базу данных.
- Не строить итоговые отчёты на ручных Excel-формулах.
- Не считать ответ LLM подтверждённым фактом без проверки по нормативному контексту.
- Не выдавать нормативную ссылку, отсутствующую в загруженной нормативной базе.
- Не отправлять сообщение до фиксации события уведомления в базе данных.
- Не менять бизнес-правила без явного указания пользователя.
- Не смешивать бизнес-логику с кодом Telegram или MAX.
- Не использовать реальные токены и пароли в коде.
- Не удалять исходные PDF, фотографии и акты после обработки.
- Не создавать дубли при повторном запуске любого пайплайна.
- Не изменять статус предписания без проверки роли, принадлежности, допустимости перехода и записи в историю.
- Не использовать СНиП/СП/ГОСТ ссылки, которых нет в загруженной нормативной базе (см. Шаг 7).

## 0.4. Обязательное правило уточнений

Если в требованиях есть противоречие, неоднозначность или недостающие данные, остановись и задай уточняющий вопрос.

Особенно обязательно спрашивай перед реализацией:

1. Какой именно API MAX используется.
2. Кто является получателем уведомления от Заказчика: Подрядчик, ответственный сотрудник Подрядчика, несколько получателей.
3. Должно ли предписание от Заказчика отправляться Подрядчику сразу после OCR или только после проверки инспектором (по умолчанию — только после проверки).
4. Какие статусы считаются допустимыми переходами (по умолчанию — см. Шаг 4).
5. Может ли Подрядчик изменять данные предписания или только менять статус и добавлять вложения (по умолчанию — только статус и вложения).
6. Требуется ли юридически значимое подтверждение получения сообщения.
7. Должен ли один подрядчик иметь несколько получателей в Telegram/MAX (по умолчанию — да, через `recipient_channels`).
8. Нужно ли создавать отдельное предписание на каждый пункт бумажного документа или одно предписание с несколькими пунктами (по умолчанию — одно предписание, пункты внутри).

Если ответ на вопрос не получен, используй безопасное значение по умолчанию и явно укажи его в коде и документации.

---

# 1. Контекст системы

## 1.1. Назначение

Система автоматизирует учёт и контроль исполнения предписаний строительного контроля.

Два источника предписаний:

1. `customer` — предписание Заказчика, полученное на бумаге и оцифрованное сканированием.
2. `internal` — внутреннее предписание Генподрядчика, созданное в электронной системе и направленное Подрядчику.

Несмотря на различный источник, бизнес-правила исполнения и уведомлений для обоих типов одинаковы.

## 1.2. Основные цели

- исключить повторный ручной ввод данных;
- исключить зависимость от Google Sheets;
- сделать базу данных единым источником истины;
- автоматически распознавать бумажные предписания;
- автоматически предлагать нормативные ссылки;
- обеспечить обязательную проверку данных инспектором;
- отслеживать сроки устранения;
- уведомлять участников через Telegram/MAX;
- фиксировать историю всех действий;
- формировать достоверные отчёты и дашборды;
- сохранять исходные документы и доказательства устранения.

## 1.3. Участники

| Роль | Ключевые действия |
|---|---|
| Инспектор строительного контроля | проверяет OCR, подтверждает регистрацию, проверяет нормативные ссылки, назначает ответственного, принимает устранение, закрывает/возвращает предписание |
| Ответственный Подрядчика | получает уведомления, переводит в `in_work`, добавляет комментарии и вложения, переводит в `fixed_pending_review` |
| Руководитель / менеджер объекта | получает уведомления (новые, просроченные, возврат, закрытие) согласно настройкам проекта |
| Администратор системы | создаёт пользователей, назначает роли, ведёт справочники, управляет настройками |
| Система | регистрирует документы, запускает OCR/LLM/RAG, создаёт и отправляет уведомления, повторяет неудачные отправки, формирует отчёты, ведёт журнал |

## 1.4. Типы предписаний

```python
class PrescriptionType(str, Enum):
    CUSTOMER = "customer"
    INTERNAL = "internal"
```

### Поток предписания Заказчика

```text
Бумажный документ
    ↓
Пакетное сканирование
    ↓
Отдельный PDF-файл
    ↓
OCR
    ↓
Извлечение полей
    ↓
Проверка инспектором
    ↓
Регистрация в БД
    ↓
Уведомления Подрядчику и Инспектору
```

### Поток внутреннего предписания

```text
Веб-форма / Telegram / MAX
    ↓
Ввод текста или голосового сообщения
    ↓
Распознавание речи, если используется голос
    ↓
LLM-извлечение полей
    ↓
Предложение нормативных ссылок через RAG
    ↓
Проверка инспектором
    ↓
Регистрация в БД
    ↓
Уведомления Подрядчику и Инспектору
```

## 1.5. Статусы предписаний

```python
class PrescriptionStatus(str, Enum):
    DRAFT_FOR_SIGNATURE = "draft_for_signature"  # только для internal до подписанного скана
    NEW = "new"
    UNDER_REVIEW = "under_review"
    IN_WORK = "in_work"
    FIXED_PENDING_REVIEW = "fixed_pending_review"
    CLOSED = "closed"
    REJECTED = "rejected"
```

| Статус | Назначение |
|---|---|
| `draft_for_signature` | Внутреннее предписание подготовлено, печатная версия сформирована; ожидаются подписи и загрузка скана |
| `new` | Зарегистрировано, но ещё не проверено инспектором |
| `under_review` | Инспектор проверяет данные, нормативы и вложения |
| `in_work` | Подрядчик выполняет устранение |
| `fixed_pending_review` | Подрядчик сообщил об устранении, требуется проверка инспектора |
| `closed` | Инспектор подтвердил устранение |
| `rejected` | Предписание отклонено, снято или признано некорректным |

`rejected` не используется для обычного возврата на доработку. Возврат — только переход `fixed_pending_review → in_work` с обязательным комментарием инспектора.

## 1.6. Правило «OCR/LLM — не подтверждение»

Все значения, полученные OCR или LLM, имеют статус `requires_review`. До проверки инспектором:

- нормативная ссылка не считается подтверждённой;
- запись не используется в итоговом отчёте как полностью проверенная;
- отправка Подрядчику не выполняется (по умолчанию запрещено, исключение включается настройкой).

---

# 2. Шаги реализации

Возможно параллелить локальные независимые шаги (2–3), но запуск любого шага в эксплуатацию — только после прохождения Шага 13 (тесты).

---

## Шаг 1. Окружение и каркас проекта

### Цель

Создать готовый к работе локальный репозиторий `prescriptions_system`: структура каталогов, конфигурация, окружение Python, скрипты установки моделей и первичной настройки.

### Артефакты

- структура каталогов (см. пример);
- `requirements.txt` (пиннинг версий);
- `config.yaml` + `.env.example`;
- `src/config.py` (Pydantic Settings);
- `scripts/setup_local.ps1` и `scripts/setup_local.sh` (создание venv, установка зависимостей, pull моделей);
- `scripts/pull_models.sh` / `.ps1` (`ollama pull qwen2.5:7b-instruct`, `ollama pull bge-m3`);
- `README.md` (краткая инструкция), `data/` директории (inbox/processed/storage/uploads).

### Ключевые решения

- Python 3.11+, venv, `.env` для секретов.
- Структура `src/` по доменам: `db`, `prescriptions`, `users`, `ocr`, `llm`, `rag`, `notifications`, `bots`, `web`, `reports`.
- SQLite в WAL-режиме; путь и параметры — из конфига.
- Ollama локально на `localhost:11434`.
- Все пути к данным задаются в конфиге, не хардкодятся.

### Примеры

Структура каталогов:

```text
prescriptions_system/
  .env.example
  config.yaml
  requirements.txt
  README.md
  src/
    config.py
    db/
      base.py
      engine.py
      models.py
      repositories.py
    users/
      service.py
      schemas.py
      admin_routes.py
    prescriptions/
      schemas.py
      service.py
      status_rules.py
    ocr/
      pipeline.py
      parsers.py
      file_registry.py
    llm/
      client.py
      prompts.py
      extract_fields.py
      extract_norm_refs.py
    rag/
      ingest_norms.py
      embeddings.py
      search.py
      recommend.py
    notifications/
      enums.py
      service.py
      templates.py
      outbox.py
      worker.py
      router.py
      adapters/
        base.py
        telegram.py
        max.py
    bots/
      telegram_bot.py
      max_bot.py
      handlers.py
    web/
      app.py
      routes/
      templates/
      static/
    reports/
      generator.py
      queries.py
  tests/
  docs/
  data/
    prescriptions/
      inbox/customer/
      processed/
      storage/
    uploads/
  scripts/
```

Пример `config.yaml`:

```yaml
app:
  name: prescriptions_system
  debug: false

db:
  url: sqlite:///data/prescriptions.db
  wal: true

ollama:
  base_url: http://localhost:11434
  llm_model: qwen2.5:7b-instruct
  embed_model: bge-m3
  timeout_s: 300

rag:
  collection: norm_docs
  chunk_size_tokens: 500
  chunk_overlap_tokens: 50
  top_k: 5

ocr:
  workspace: data/ocr_workspace
  langs: ["ru"]

storage:
  inbox: data/prescriptions/inbox
  processed: data/prescriptions/processed
  uploads: data/uploads

notifications:
  max_attempts: 5
  retry_delays_s: [60, 300, 900, 3600]
```

---

## Шаг 2. Модель данных (SQLAlchemy 2.x + Alembic)

### Цель

Спроектировать и реализовать полную схему БД: справочники, предписания, история, RAG, уведомления, служебные таблицы. Обеспечить идемпотентность и уникальность ключевых записей.

### Артефакты

- `src/db/models.py` — ORM-модели;
- `src/db/engine.py`, `src/db/base.py`;
- Alembic: `alembic.ini`, `migrations/env.py`, первая ревизия;
- DDL-экспорт `docs/schema.sql` (для аудита).

Таблицы:

- справочники: `objects`, `contractors`, `users`, `recipient_channels`, `norm_docs`;
- предписания: `prescriptions`, `prescription_items`, `prescription_norm_refs`, `prescription_attachments`, `prescription_history`;
- RAG: `norm_chunks`, `norm_chunk_embeddings` (ChromaDB хранит эмбеддинги персистентно; таблица — для метаданных/линков);
- уведомления: `notifications`, `notification_attempts`;
- роли: `user_role_change_log`;
- служебные: `llm_rag_logs`, `processed_files`, `system_config`.

### Ключевые решения

- SQLAlchemy 2.x (typed ORM), миграции через Alembic.
- Enum-поля — как SQLAlchemy `Enum` с value из Python-классов; JSON-поля — `JSONB`/`JSON`.
- Уникальные ограничения (идемпотентность):
  - `prescriptions`: `doc_number + doc_date + object_id + prescription_type`;
  - `processed_files`: `sha256`;
  - `notifications`: `(prescription_id, event_type, recipient_type, recipient_id, event_version)`.
- Внешние ключи с индексом; каскады только там, где безопасно (история/уведомления не удаляются каскадом от предписания).
- Контакты получателя хранятся в отдельной таблице `recipient_channels`, а не набором полей в `users`/`contractors`.

### Примеры

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(unique=True, index=True)
    full_name: Mapped[str]
    role: Mapped[UserRole] = mapped_column(Enum(UserRole))
    email: Mapped[str | None]
    phone: Mapped[str | None]
    preferred_notification_channel: Mapped[str | None]
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id"))
    object_id: Mapped[int | None] = mapped_column(ForeignKey("objects.id"))
    password_hash: Mapped[str]
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
```

```python
class Prescription(Base):
    __tablename__ = "prescriptions"
    __table_args__ = (
        UniqueConstraint(
            "doc_number", "doc_date", "object_id", "prescription_type",
            name="uq_prescription_doc",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_type: Mapped[PrescriptionType] = mapped_column(Enum(PrescriptionType))
    doc_number: Mapped[str]
    doc_date: Mapped[date]
    object_id: Mapped[int] = mapped_column(ForeignKey("objects.id"))
    contractor_id: Mapped[int] = mapped_column(ForeignKey("contractors.id"))
    description: Mapped[str]
    deadline: Mapped[date | None]
    responsible_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    inspector_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[PrescriptionStatus] = mapped_column(Enum(PrescriptionStatus), default=PrescriptionStatus.NEW)
    source_file_path: Mapped[str | None]
    source_file_hash: Mapped[str | None]
    ocr_text: Mapped[str | None]
    ocr_confidence: Mapped[float | None]
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None]
    closed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None]
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
```

```python
class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id"), index=True)
    event_type: Mapped[NotificationEvent] = mapped_column(Enum(NotificationEvent))
    recipient_type: Mapped[str]
    recipient_id: Mapped[int]
    channel: Mapped[NotificationChannel] = mapped_column(Enum(NotificationChannel))
    message_text: Mapped[str]
    status: Mapped[NotificationStatus] = mapped_column(Enum(NotificationStatus), default=NotificationStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None]
    external_message_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None]
    next_attempt_at: Mapped[datetime | None]
```

```python
class PrescriptionHistory(Base):
    __tablename__ = "prescription_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id"), index=True)
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    change_type: Mapped[str]
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    comment: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

История обязательна для: создания, изменения статуса, срока, ответственного, нормативных ссылок, добавления вложений, закрытия, возврата на доработку.

---

## Шаг 3. Пользователи и роли

### Цель

Реализовать управление пользователями и ролями: только администратор создаёт учётные записи и назначает роли; самостоятельная регистрация не предусмотрена; все изменения ролей логируются.

### Артефакты

- `src/db/models.py`: `UserRole` enum, расширенная модель `User`, модель `UserRoleChangeLog`;
- `src/users/service.py`: `create_user`, `change_user_role`, `deactivate_user`, CSV-импорт;
- `src/users/admin_routes.py` + шаблоны: страница `/admin/users`;
- `scripts/create_first_admin.py` (интерактивный и env-режим);
- тесты: запрет самостоятельной смены роли, уникальность логина.

### Ключевые решения

- Роли: `admin`, `inspector`, `contractor_responsible`, `manager`.
- Только `admin` может создавать пользователей, менять роли, блокировать (`is_active`).
- Временный пароль хешируется (bcrypt через `passlib`), при первом входе — смена пароля.
- Открытая регистрация и смена роли через бота запрещены.
- Один подрядчик может иметь несколько ответственных (несколько `users` с одним `contractor_id` или таблица m2m `contractor_users`).
- Логирование смены ролей в `user_role_change_log`.
- Первый администратор создаётся скриптом (интерактивно или из env `FIRST_ADMIN_*`); если админ уже есть — скрипт завершается без изменений.

### Примеры

```python
class UserRole(str, Enum):
    ADMIN = "admin"
    INSPECTOR = "inspector"
    CONTRACTOR_RESPONSIBLE = "contractor_responsible"
    MANAGER = "manager"
```

```python
# src/users/service.py

from sqlalchemy.orm import Session
from src.db.models import User, UserRole, UserRoleChangeLog
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_user(
    db: Session,
    login: str,
    full_name: str,
    role: UserRole,
    email: str | None = None,
    phone: str | None = None,
    telegram_chat_id: str | None = None,
    max_user_id: str | None = None,
    contractor_id: int | None = None,
    object_id: int | None = None,
) -> User:
    existing = db.query(User).filter(User.login == login).first()
    if existing:
        raise ValueError(f"Пользователь с логином {login} уже существует")

    temp_password = generate_temp_password()
    hashed_password = pwd_context.hash(temp_password)

    user = User(
        login=login,
        full_name=full_name,
        role=role,
        email=email,
        phone=phone,
        telegram_chat_id=telegram_chat_id,
        max_user_id=max_user_id,
        contractor_id=contractor_id,
        object_id=object_id,
        password_hash=hashed_password,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def change_user_role(
    db: Session,
    user_id: int,
    new_role: UserRole,
    changed_by_user_id: int,
) -> User:
    user = db.get(User, user_id)
    if not user:
        raise ValueError("Пользователь не найден")

    admin = db.get(User, changed_by_user_id)
    if not admin or admin.role != UserRole.ADMIN:
        raise PermissionError("Только администратор может менять роли")

    old_role = user.role
    user.role = new_role
    db.add(
        UserRoleChangeLog(
            user_id=user.id,
            old_role=old_role.value,
            new_role=new_role.value,
            changed_by_user_id=changed_by_user_id,
        )
    )
    db.commit()
    db.refresh(user)
    return user
```

Скрипт первого администратора `scripts/create_first_admin.py`:

```bash
# интерактивный режим
python scripts/create_first_admin.py

# или через переменные окружения
FIRST_ADMIN_LOGIN=admin
FIRST_ADMIN_PASSWORD=SecurePassword123
FIRST_ADMIN_FULL_NAME=Администратор Системы
FIRST_ADMIN_EMAIL=admin@company.com
```

---

## Шаг 4. Ручное создание предписания и статусная машина

### Цель

Реализовать создание внутреннего предписания через веб-форму и единую машину переходов статусов для обоих типов предписаний с проверкой ролей, обязательными комментариями и записью истории.

### Артефакты

- `src/prescriptions/schemas.py` (Pydantic: `PrescriptionCreate`, `StatusChange`);
- `src/prescriptions/service.py`: `create_prescription`, `change_status`;
- `src/prescriptions/status_rules.py`: допустимые переходы и кто их выполняет;
- `src/prescriptions/print_service.py`: формирование версии PDF, хеширование, архив печатных версий;
- `src/prescriptions/signed_scan_service.py`: приём, OCR и проверка подписанного скана;
- веб-форма создания предписания (Шаг 9), история (таблица `prescription_history`);
- тесты переходов (Шаг 13).

### Ключевые решения

- Для внутреннего предписания обязательный путь до начала исполнения:

```text
internal draft_for_signature → new  (Инспектор после загрузки и проверки подписанного скана)
```

- Допустимые переходы после регистрации/подтверждения вручения:

```text
new → under_review          (Инспектор)
new → in_work               (Подрядчик или Инспектор)
new → rejected              (Инспектор)
under_review → in_work      (Инспектор)
under_review → rejected     (Инспектор)
in_work → fixed_pending_review  (Подрядчик)
in_work → rejected          (Инспектор)
fixed_pending_review → closed   (Инспектор)
fixed_pending_review → in_work  (Инспектор, обязателен комментарий)
closed → другое состояние       (только администратор)
rejected → другое состояние     (только администратор)
```

- При переходе `fixed_pending_review → in_work` обязательны: комментарий инспектора, причина возврата, при необходимости список недостающих документов/фото.
- Статус меняется только через `change_status` (единую точку входа): проверка роли/принадлежности к подрядчику → проверка допустимости перехода → запись в историю → генерация события уведомления.
- Уведомления формируются транзакционно в том же коммите (outbox).
- Событие для уведомления не генерируется, если перехода нет в списке событий (см. Шаг 8).

### Примеры

```python
def change_status(
    db: Session,
    prescription: Prescription,
    new_status: PrescriptionStatus,
    changed_by_user: User,
    comment: str | None = None,
) -> Prescription:
    old_status = prescription.status

    validate_status_transition(
        old_status=old_status,
        new_status=new_status,
        changed_by_user=changed_by_user,
        prescription=prescription,
        comment=comment,
    )

    prescription.status = new_status

    db.add(
        PrescriptionHistory(
            prescription_id=prescription.id,
            changed_by_user_id=changed_by_user.id,
            change_type="status_changed",
            old_value={"status": old_status.value},
            new_value={"status": new_status.value, "comment": comment},
            comment=comment,
        )
    )

    db.add(prescription)

    event = build_status_event(old_status=old_status, new_status=new_status)
    if event is not None:
        create_notification_outbox_records(
            db=db,
            prescription=prescription,
            event=event,
            comment=comment,
        )

    db.commit()
    db.refresh(prescription)
    return prescription
```

```python
# src/prescriptions/status_rules.py (фрагмент)

TRANSITIONS: dict[PrescriptionStatus, dict[PrescriptionStatus, tuple[str, ...]]] = {
    PrescriptionStatus.NEW: {
        PrescriptionStatus.UNDER_REVIEW: (UserRole.INSPECTOR,),
        PrescriptionStatus.IN_WORK: (UserRole.CONTRACTOR_RESPONSIBLE, UserRole.INSPECTOR),
        PrescriptionStatus.REJECTED: (UserRole.INSPECTOR,),
    },
    PrescriptionStatus.UNDER_REVIEW: {
        PrescriptionStatus.IN_WORK: (UserRole.INSPECTOR,),
        PrescriptionStatus.REJECTED: (UserRole.INSPECTOR,),
    },
    PrescriptionStatus.IN_WORK: {
        PrescriptionStatus.FIXED_PENDING_REVIEW: (UserRole.CONTRACTOR_RESPONSIBLE,),
        PrescriptionStatus.REJECTED: (UserRole.INSPECTOR,),
    },
    PrescriptionStatus.FIXED_PENDING_REVIEW: {
        PrescriptionStatus.CLOSED: (UserRole.INSPECTOR,),
        PrescriptionStatus.IN_WORK: (UserRole.INSPECTOR,),  # обязателен comment
    },
}

REQUIRED_COMMENT_TRANSITIONS = {frozenset((PrescriptionStatus.FIXED_PENDING_REVIEW, PrescriptionStatus.IN_WORK))}
```

---

## Шаг 5. OCR-пайплайн для предписаний Заказчика

### Цель

Автоматически принимать пакетные сканы, распознавать русский текст, сохранять оригиналы и метаданные, исключать повторную обработку одного файла.

### Артефакты

- `src/ocr/file_registry.py` — SHA-256 и учёт обработанных файлов;
- `src/ocr/pipeline.py` — оркестрация этапов;
- `src/ocr/parsers.py` — извлечение fast-полей (номер, дата, ссылки) регулярными выражениями;
- `scripts/run_ocr_pipeline.py` — запуск по расписанию или вручную;
- таблица `processed_files`;
- тестовые PDF/JPG.

### Ключевые решения

- Входная папка: `data/prescriptions/inbox/customer/`.
- Идемпотентность — по SHA-256 файла (проверить в `processed_files` до обработки).
- Текстовый PDF → PyMuPDF (быстрый путь); сканы → PaddleOCR (русский).
- OCR-текст и confidence сохраняются в `prescriptions.ocr_text` / `ocr_confidence`.
- Регулярные выражения дают черновые поля; финальные значения и нормативные ссылки — LLM/инспектором.
- Оригинал никогда не удаляется; после обработки копия помещается в `processed` (с тем же хешем).
- Результат — запись в БД со статусом `new`, затем переходит на проверку инспектору; уведомления — только после подтверждения (см. Шаг 8).

### Примеры

Этапы (соответствуют §16.2 переработанного промпта):

```text
1. Найти новые PDF в inbox.
2. Вычислить SHA-256.
3. Проверить, нет ли хеша в processed_files -> если есть, пропустить.
4. Сохранить оригинал в storage.
5. Разбить документ на страницы.
6. Выполнить OCR (сканы) или вытащить текст (PyMuPDF).
7. Сохранить OCR-текст и confidence.
8. Извлечь fast-поля регулярными выражениями.
9. При необходимости вызвать LLM (Шаг 6).
10. Создать запись prescriptions со статусом new.
11. Перевести на проверку инспектору (under_review).
12. После подтверждения сформировать уведомления.
13. Переместить копию в processed.
```

```python
# src/ocr/file_registry.py

def is_already_processed(db: Session, sha256: str) -> bool:
    return db.query(ProcessedFile).filter(ProcessedFile.sha256 == sha256).first() is not None


def mark_processed(db: Session, sha256: str, file_path: str) -> None:
    db.add(ProcessedFile(sha256=sha256, original_path=file_path))
    db.commit()
```

```python
class ProcessedFile(Base):
    __tablename__ = "processed_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(unique=True, index=True)
    original_path: Mapped[str]
    processed_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

---

## Шаг 6. Локальная LLM: извлечение полей и нормализация

### Цель

Через локальную LLM (Ollama `qwen2.5:7b-instruct`) извлекать структурированные поля из OCR-текста и текста, нормализовать описание нарушения, выделять пункты, извлекать явные нормативные ссылки и готовить запросы для RAG.

### Артефакты

- `src/llm/client.py` — обёртка над HTTP API Ollama (таймауты, ретраи, JSON-режим);
- `src/llm/prompts.py` — версионированные промпты;
- `src/llm/extract_fields.py` — извлечение полей предписания;
- `src/llm/extract_norm_refs.py` — выявление явных нормативных ссылок в тексте;
- таблица `llm_rag_logs` (модель, версия промпта, вход/выход, время, confidence);
- тесты с fake-LLM.

### Ключевые решения

- Ответ LLM — строгий JSON по зафиксированной JSON Schema; валидация через Pydantic; при ошибке — повтор с сообщением об ошибке, затем fallback.
- LLM не имеет права: подтверждать нормативную ссылку; закрывать предписание; менять статус; удалять данные; заменять решение инспектора.
- Каждый вызов логируется в `llm_rag_logs` с версией промпта. Смена промпта увеличивает версию — метаданные результата всегда воспроизводимы.
- Учитывая «слабое железо»: таймаут до 300 с, батчи постранично, один запрос на предписание (без дробления), повторные вызовы не создают дубли записей (идемпотентность по `prescription_id + field`).
- Поля `requires_review` до подтверждения инспектором.

### Примеры

Промпт извлечения (RU), версия `extract_v1`:

```text
Ты извлекаешь структурированные данные из предписания строительного контроля.
Вход: OCR-текст документа.
Верни ТОЛЬКО JSON без пояснений по схеме:
{
  "doc_number": string | null,
  "doc_date": "YYYY-MM-DD" | null,
  "object_name": string | null,
  "contractor_name": string | null,
  "description": string,
  "violation": string | null,
  "deadline": "YYYY-MM-DD" | null,
  "responsible_person": string | null,
  "items": [
    {"text": string, "deadline": "YYYY-MM-DD" | null, "explicit_norm_refs": [string]}
  ],
  "confidence": number  // 0..1
}
Если значение отсутствует в тексте — ставь null. Не выдумывай номера и даты.
```

JSON-ответ LLM:

```json
{
  "doc_number": "П-14/2026",
  "doc_date": "2026-09-01",
  "object_name": "Склад №3",
  "contractor_name": "ООО СтройМонтаж",
  "description": "Не обеспечена защита арматуры от коррозии.",
  "violation": "Отсутствие защитного слоя бетона",
  "deadline": "2026-09-30",
  "responsible_person": "Иванов Иван",
  "items": [
    {
      "text": "Обеспечить защитный слой бетона в пролёте 2",
      "deadline": "2026-09-30",
      "explicit_norm_refs": ["СП 63.13330.2018"]
    }
  ],
  "confidence": 0.82
}
```

```python
# src/llm/client.py (фрагмент)

class OllamaClient:
    def __init__(self, base_url, model, timeout_s):
        self.base_url = base_url
        self.model = model
        self.timeout_s = timeout_s

    def chat_json(self, prompt: str, schema: dict, temperature: float = 0.0) -> dict:
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "options": {"temperature": temperature},
            },
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])
```

---

## Шаг 7. RAG по нормативной базе (СНиП/СП/ГОСТ/СТО/ТУ)

### Цель

Загрузить нормативные документы в локальное векторное хранилище и для каждого пункта предписания получать ссылки на конкретные пункты норм с фрагментом-источником для проверки инспектором.

### Артефакты

- `src/rag/ingest_norms.py` — импорт PDF/DOCX/TXT → текст → чанки;
- `src/rag/embeddings.py` — эмбеддинги через Ollama `bge-m3`;
- `src/rag/search.py` — `search_topk`;
- `src/rag/recommend.py` — LLM-подбор норм по контексту RAG;
- таблицы `norm_docs`, `norm_chunks`; Chroma collection `norm_docs` (persistent);
- `scripts/ingest_norms.py` (CLI), тестовые нормативные файлы.

### Ключевые решения

- Источники: СНиП, СП, ГОСТ, СТО, ТУ, внутренние стандарты организации. Только документы, загруженные администратором.
- В `norm_docs`: номер, название, редакция, статус документа.
- В чанке хранятся: `document_id`, раздел, пункт, подпункт, текст, range.
- Чанкинг: ~500 токенов, пересечение 50; границы режем по разделам/пунктам.
- ChromaDB: `persist_directory` локально; коллекция `norm_docs`; метаданные чанка — JSON.
- Поиск по каждому пункту предписания → top-k фрагментов → LLM выбирает подходящие пункты.
- RAG не подтверждает: если подходящий пункт не найден — пустой результат; ссылка без записи в базе не выдаётся.
- Инспектору показывается исходный фрагмент текста нормы; предложенные ссылки отделены от подтверждённых.

### Примеры

```python
# src/rag/search.py (фрагмент)

def search_topk(
    collection: chroma.Collection,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    res = collection.query(
        query_embeddings=[embed_query(query)],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "document_id": meta["document_id"],
            "section": meta.get("section"),
            "item": meta.get("item"),
            "fragment": doc,
            "distance": dist,
        }
        for doc, meta, dist in zip(
            res["documents"][0],
            res["metadatas"][0],
            res["distances"][0],
        )
    ]
```

Промпт рекомендации норм (RU), версия `recommend_v1`:

```text
Ты подбираешь пункты нормативных документов к пункту предписания.
Даны фрагменты из загруженной базы (СНиП/СП/ГОСТ/СТО/ТУ). Используй ТОЛЬКО эти фрагменты.
Выбери не более 3 релевантных. Если ничего не подходит — верни пустой список.

Верни ТОЛЬКО JSON: [{"doc_code": "...", "item": "...", "title": "...", "reason": "..."}]
```

JSON-ответ RAG:

```json
[
  {
    "doc_code": "СП 63.13330.2018",
    "item": "п. 5.3",
    "title": "Бетонные и железобетонные конструкции. Защитный слой бетона",
    "reason": "Пункт напрямую регламентирует толщину защитного слоя арматуры"
  }
]
```

Хранение норм:

```python
class NormDoc(Base):
    __tablename__ = "norm_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True)          # СП 63.13330.2018
    title: Mapped[str]
    edition: Mapped[str | None]
    status: Mapped[str]                                     # действующий / актуализированный / отменён
    source_file: Mapped[str]
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NormChunk(Base):
    __tablename__ = "norm_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("norm_docs.id"))
    section: Mapped[str | None]
    item: Mapped[str | None]                                # пункт / подпункт
    text: Mapped[str]
    chunk_index: Mapped[int]
```

---

## Шаг 8. Уведомления: outbox, Telegram, повторные отправки

### Цель

Реализовать надёжную доставку уведомлений: событие → запись `notifications` (pending) → воркер → адаптер Telegram/MAX → фиксация результата; повторные попытки с бэккоффом; отсутствие дублей.

### Артефакты

- `src/notifications/enums.py` — события и статусы;
- `src/notifications/service.py` — определение получателей, формирование сообщений, создание записей (outbox);
- `src/notifications/outbox.py`, `src/notifications/worker.py` — цикл доставки;
- `src/notifications/templates.py` — тексты сообщений;
- `src/notifications/adapters/base.py`, `telegram.py`, `max.py` (max — заглушка по контракту);
- `src/notifications/router.py` — выбор канала;
- таблицы `notifications`, `notification_attempts`, `recipient_channels`;
- тест идемпотентности (Шаг 13).

### Ключевые решения

- Принцип: бизнес-логика не вызывает Telegram/MAX API напрямую — только через outbox.
- Статусы: `pending`, `processing`, `sent`, `failed`, `no_channel`, `cancelled`.
- Каналы: `telegram`, `max`. Выбор канала — `router`: предпочтительный доступен → он; нет → другой доступный; нет каналов → `no_channel` (запись остаётся до исправления контакта).
- Идемпотентность: уникальный ключ `(prescription_id, event_type, recipient_type, recipient_id, event_version)`. `event_version` растёт при повторном существенном событии (первое устранение=1, возврат=2, повторное устранение=3).
- Retry: 1-я — 1 мин, 2-я — 5 мин, 3-я — 15 мин, 4-я — 1 час, далее по настройке; после лимита — `failed`, служебное уведомление администратору, запись не удаляется.
- Адаптер отвечает только за авторизацию, отправку сообщения/файла, получение `external_message_id`, возврат ошибки.

### Примеры

События (`NotificationEvent`):

```python
PRESCRIPTION_CREATED = "prescription_created"
STATUS_CHANGED_TO_UNDER_REVIEW = "status_changed_to_under_review"
STATUS_CHANGED_TO_IN_WORK = "status_changed_to_in_work"
STATUS_CHANGED_TO_FIXED_PENDING_REVIEW = "status_changed_to_fixed_pending_review"
RETURNED_TO_IN_WORK = "returned_to_in_work"
PRESCRIPTION_CLOSED = "prescription_closed"
PRESCRIPTION_REJECTED = "prescription_rejected"
DEADLINE_CHANGED = "deadline_changed"
RESPONSIBLE_CHANGED = "responsible_changed"
ATTACHMENT_ADDED = "attachment_added"
INTERNAL_PRINT_VERSION_GENERATED = "internal_print_version_generated"
INTERNAL_SIGNED_SCAN_UPLOADED = "internal_signed_scan_uploaded"
INTERNAL_SIGNED_SCAN_VERIFIED = "internal_signed_scan_verified"
DEADLINE_APPROACHING = "deadline_approaching"
DEADLINE_OVERDUE = "deadline_overdue"
```

Матрица уведомлений:

| Событие | Инициатор | Получатель | Обязательное действие |
|---|---|---|---|
| `prescription_created` | Система/Инспектор | Подрядчик, Инспектор | Ознакомиться |
| `status_changed_to_under_review` | Инспектор | Подрядчик, Инспектор | Дождаться проверки |
| `status_changed_to_in_work` | Подрядчик | Инспектор | Зафиксировать начало работы |
| `status_changed_to_fixed_pending_review` | Подрядчик | Инспектор | Проверить устранение |
| `returned_to_in_work` | Инспектор | Подрядчик | Выполнить доработку |
| `prescription_closed` | Инспектор | Подрядчик | Ознакомиться с закрытием |
| `prescription_rejected` | Инспектор | Подрядчик, Инспектор | Ознакомиться с причиной |
| `deadline_changed` | Инспектор | Подрядчик | Соблюдать новый срок |
| `responsible_changed` | Инспектор/Администратор | Новый ответственный | Принять работу |
| `deadline_approaching` | Система | Подрядчик, Инспектор | Ускорить устранение |
| `deadline_overdue` | Система | Подрядчик, Инспектор, руководитель | Принять меры |

Пример текста сообщения (`prescription_created`):

```text
Зарегистрировано новое предписание № {номер}.

Объект: {объект}
Подрядчик: {подрядчик}
Срок устранения: {срок}

Карточка: {ссылка}
```

Воркер (фрагмент):

```python
def process_pending_notifications(db: Session) -> None:
    for notif in db.query(Notification).filter(
        Notification.status == NotificationStatus.PENDING,
        Notification.next_attempt_at.is_(None)
        | (Notification.next_attempt_at <= datetime.now(UTC)),
    ).all():
        channel = resolve_channel(notif)
        if channel is None:
            mark_no_channel(db, notif)
            continue

        adapter = get_adapter(notif.channel)
        try:
            external_id = adapter.send(notif.message_text, notif.recipient)
            notif.status = SENT
            notif.external_message_id = external_id
            notif.sent_at = datetime.now(UTC)
        except Exception as exc:
            notif.status = FAILED
            notif.attempt_count += 1
            notif.last_error = str(exc)
            notif.next_attempt_at = compute_next_attempt(notif.attempt_count)
        db.commit()
```

`recipient_channels`:

```python
class RecipientChannel(Base):
    __tablename__ = "recipient_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_type: Mapped[str]          # user / contractor
    recipient_id: Mapped[int]
    channel: Mapped[NotificationChannel] = mapped_column(Enum(NotificationChannel))
    address: Mapped[str]                 # telegram chat_id / max user_id
    is_preferred: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
```

---

## Шаг 9. Веб-интерфейс (FastAPI + Jinja2)

### Цель

Предоставить ролевой веб-интерфейс: реестр, карточка предписания, проверка OCR, проверка и подтверждение нормативных ссылок, вложения, смена статусов, история, настройки контактов и пользователей.

### Артефакты

- `src/web/app.py` (FastAPI), `src/web/routes/` (modules per domain);
- шаблоны Jinja2: `templates/prescriptions/*`, `templates/admin/*`, `templates/reports/*`;
- `src/web/static/` (локальные стили и Chart.js в `vendor/` — без CDN);
- сессии/авторизация (local auth по `users`, bcrypt);
- RBAC-зависимость для ролей (admin/inspector/contractor_responsible/manager).

### Ключевые решения

- Разделы по §23 переработанного промпта: реестр предписаний, карточка, проверка OCR, проверка нормативных ссылок, фото/акты, смена статуса, история изменений, история уведомлений, отчёты, настройки контактов, `admin/users`.
- Подрядчик (contractor_responsible) редактирует ТОЛЬКО статус и вложения; данные предписания не изменяет.
- В карточке нормативные ссылки разбиты на «предложено LLM/RAG» (требует проверки) и «подтверждено инспектором».
- Автоподстановка норм (RAG) вызывается при редактировании пунктов карточки.
- Валидация переходов и уведомления выполняются сервисами (Шаг 4/8), контроллеры «тонкие».
- В карточке внутреннего предписания для инспектора доступны кнопки: `Сформировать печатную версию`, `Печать`, `Загрузить подписанный скан`, `Проверить OCR скана`, `Подтвердить скан и вручение`.
- Кнопка `Печать` вызывает серверное формирование или получает уже сформированный PDF, открывает print-friendly страницу/загрузку PDF; браузерный `window.print()` допустим только как интерфейсная функция, но печатная версия должна быть предварительно зафиксирована на сервере.
- Кнопка `Печать` доступна только инспектору/администратору, создаёт аудит `print_requested` и не меняет содержание документа. Подрядчик не может печатать новую версию или заменять подписанный скан.

### Примеры

Роут-карточка (фрагмент):

```python
@router.get("/prescriptions/{prescription_id}")
@require_role(UserRole.INSPECTOR, UserRole.ADMIN)
def prescription_card(request: Request, prescription_id: int, db: Session = Depends(get_db)):
    prescription = db.get(Prescription, prescription_id)
    history = db.query(PrescriptionHistory).filter_by(prescription_id=prescription_id).order_by(PrescriptionHistory.created_at.desc()).all()
    notifications = db.query(Notification).filter_by(prescription_id=prescription_id).order_by(Notification.created_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "prescriptions/card.html",
        {"prescription": prescription, "history": history, "notifications": notifications},
    )
```

Jinja-блок нормативных ссылок:

```jinja
<div class="norm-block">
  <h3>Нормативные ссылки</h3>
  <div class="norm-suggested">
    <p>Предложено LLM/RAG (требует проверки):</p>
    {% for ref in prescription.suggested_norm_refs %}
      <label>
        <input type="checkbox" name="confirm_norm_refs" value="{{ ref.id }}">
        {{ ref.doc_code }} {{ ref.item }} — {{ ref.fragment|truncate(80) }}
      </label>
    {% endfor %}
  </div>
  <ul class="norm-confirmed">
    {% for ref in prescription.confirmed_norm_refs %}
      <li>{{ ref.doc_code }} {{ ref.item }} (подтверждено, {{ ref.confirmed_by.full_name }})</li>
    {% endfor %}
  </ul>
</div>
```

---

## Шаг 10. Telegram-бот

### Цель

Дать Подрядчику и Инспектору основные действия через Telegram (кнопки и команды) с полной проверкой ролей и через сервисные функции, а не прямой сменой статусов.

### Артефакты

- `src/bots/telegram_bot.py`, `src/bots/handlers.py`;
- `scripts/run_bot.py`;
- команды (см. ниже);
- кнопки-инлайн: принять в работу / сообщить об устранении / вернуть на доработку / закрыть;
- история доставки через таблицы уведомлений (Шаг 8).

### Ключевые решения

- Команды Подрядчика:

```text
/start
/my_prescriptions
/open <номер>
/in_work <номер>
/fixed <номер>
/comment <номер> <текст>
```

- Команды Инспектора:

```text
/open <номер>
/close <номер> <комментарий>
/rework <номер> <комментарий>
/reject <номер> <комментарий>
```

- Любая команда проходит: авторизацию пользователя → проверку роли → принадлежность к подрядчику (для contractor_responsible) → допустимость перехода → запись в историю → outbox-уведомление.
- Вложенные фото в состоянии `in_work`/`fixed_pending_review` принимаются как вложения к предписанию.
- Чат связывается с пользователем через `recipient_channels` (telegram).

### Примеры

```python
# src/bots/handlers.py (фрагмент: Подрядчик берёт в работу)

@bot.message_handler(commands=["in_work"])
def cmd_in_work(message: Message):
    user = link_user_by_telegram(message.from_user.id)
    if not user or user.role != UserRole.CONTRACTOR_RESPONSIBLE:
        bot.reply_to(message, "Доступ запрещён.")
        return

    m = re.match(r"/in_work\s+(\S+)", message.text)
    if not m:
        bot.reply_to(message, "Пример: /in_work <номер>")
        return

    prescription = find_prescription_by_number(m.group(1))
    if prescription is None or prescription.contractor_id != user.contractor_id:
        bot.reply_to(message, "Предписание не найдено.")
        return

    try:
        change_status(db, prescription, PrescriptionStatus.IN_WORK, user, comment=None)
        bot.reply_to(message, f"Предписание {prescription.doc_number} переведено в работу.")
    except ValueError as exc:
        bot.reply_to(message, f"Ошибка: {exc}")
```

Уведомление Инспектору при `status_changed_to_in_work`:

```text
Предписание № {номер} переведено в статус «В работе».

Объект: {объект}
Подрядчик: {подрядчик}
Ответственный: {ответственный}
Дата начала работ: {дата}

Карточка: {ссылка}
```

---

## Шаг 11. Отчёты и дашборды

### Цель

Строить достоверные отчёты и дашборды исключительно запросами к БД и формировать экспорт (CSV/XLSX/PDF) для руководства.

### Артефакты

- `src/reports/queries.py` — SQL/SQLAlchemy-запросы метрик;
- `src/reports/generator.py` — сборка отчётов (JSON → шаблон);
- `web/templates/reports/dashboard.html`, `report_pdf.html`;
- `scripts/make_report.py`;
- локальные `static/vendor/chart.umd.js` (без CDN) и WeasyPrint для PDF;
- экспорт CSV (встроенный `csv`) и XLSX.

### Ключевые решения

- Отчёты только по БД; никаких ручных формул в Excel.
- Метрики (§28 переработанного промпта):

  - общее количество предписаний;
  - предписаний Заказчика / внутренних;
  - количество: `new`, `in_work`, `fixed_pending_review`, закрытых;
  - количество просроченных (deadline < now и статус не `closed`);
  - средний срок устранения;
  - среднее время проверки;
  - количество возвратов на доработку;
  - предписания по подрядчикам;
  - нарушения по объектам;
  - частота нормативных пунктов;
  - процент отправленных уведомлений и ошибок доставки.

- PDF-отчёт: Jinja2 HTML → WeasyPrint; всё локально.
- Данные для графиков — JSON из запросов, Chart.js рендерит в браузере.

### Примеры

```sql
-- Количество предписаний по статусам
SELECT status, COUNT(*) AS cnt
FROM prescriptions
GROUP BY status;

-- Просроченные предписания
SELECT doc_number, deadline, contractor_id, status
FROM prescriptions
WHERE status NOT IN ('closed', 'rejected')
  AND deadline IS NOT NULL
  AND deadline < DATE('now');

-- Средний срок устранения (дни)
SELECT AVG(CAST(julianday(closed_at) - julianday(created_at) AS INTEGER)) AS avg_days
FROM prescriptions
WHERE status = 'closed';

-- Частота нормативных пунктов
SELECT pnr.doc_code, pnr.item, COUNT(*) AS cnt
FROM prescription_norm_refs pnr
JOIN prescription_norm_refs_map pm ON pm.ref_id = pnr.id
GROUP BY pnr.doc_code, pnr.item
ORDER BY cnt DESC
LIMIT 20;
```

Дашборд-страница: структура данных для Chart.js:

```json
{
  "statuses": { "new": 3, "in_work": 5, "fixed_pending_review": 2, "closed": 14, "rejected": 1 },
  "overdue": 1,
  "by_contractor": [["ООО СтройМонтаж", 9], ["ООО ЭлектроСеть", 6]],
  "notifications": { "sent_pct": 98.1, "failed_pct": 1.9 }
}
```

---

## Шаг 12. Надёжность, бэкапы и логирование

### Цель

Обеспечить сохранность данных: WAL, ежедневные бэкапы БД и файлов, проверка восстановления, журналирование ошибок, идемпотентность пайплайнов.

### Артефакты

- `scripts/backup.ps1` / `backup.sh` — бэкап `predictions.db` + `data/` (обязательные каталоги), ротация;
- `scripts/restore_backup.ps1` / `.sh` и чек-лист проверки восстановления;
- `docs/reliability.md` — процедуры;
- структурированное логирование (std logging + JSON-formatter во всех пайплайнах);
- таблица `system_config` для переключателей (например, «отправлять OCR без проверки: off»).

### Ключевые решения

- SQLite в WAL-режиме; при многопользовательском режиме — PostgreSQL.
- Ежедневный снапшот БД (SQLite `.backup` / VACUUM INTO) + копия файлов-оригиналов.
- Хранение оригиналов в отдельной папке, запрет удаления (бизнес-правило).
- SHA-256 для всех принимаемых документов, проверка дублей до записи.
- Транзакционная запись уведомлений (событие и outbox в одном коммите).
- Регулярная проверка восстановления из бэкапа (тест restore на отдельной копии).
- Идемпотентность любого перезапуска пайплайна (хеши, уникальные ключи).

### Примеры

```bash
# scripts/backup.sh: ежедневный бэкап БД + файлов
sqlite3 data/prescriptions.db "VACUUM INTO 'backups/prescriptions_$(date +%F).db'"
rsync -a data/ backups/files_$(date +%F)/
find backups -name 'prescriptions_*.db' -mtime +30 -delete
```

```python
# включение WAL при первом подключении
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
```

Чек-лист восстановления (документируется в `docs/reliability.md`):

1. Скопировать последний бэкап БД и файлов на чистую среду.
2. Открыть БД: `PRAGMA integrity_check;` → `ok`.
3. Сверить контрольную сумму восстановленных оригиналов с `processed_files.sha256`.
4. Проверить, что в реестре отображается последний зарегистрированный номер.
5. Запустить тестовый прогон пайплайна на копии inbox — дубли не создаются.

---

## Шаг 13. Тесты (pytest) и критерии готовности MVP

### Цель

Покрыть ключевые контракты тестами и зафиксировать критерии готовности MVP: статусная машина, идемпотентность, OCR-парсер, извлечение полей, RAG, уведомления.

### Артефакты

- `tests/conftest.py` — SQLite in-memory (или временный файл с WAL) с накатом моделей, фабрики пользователей/подрядчиков/объектов;
- `tests/test_status_rules.py`, `tests/test_ocr_parser.py`, `tests/test_internal_signed_scan.py`, `tests/test_print_service.py`, `tests/test_extract_fields.py`, `tests/test_rag.py`, `tests/test_notifications.py`, `tests/test_file_registry.py`;
- `tests/fakes/` — fake Ollama client, fake Telegram/MAX adapter (режимы: ok, fail);
- тестовые PDF/скан (нарисованный шаблон предписания).

### Ключевые решения

- Один тест — один контракт; тесты не выходят в сеть (все внешние сервисы заменены фейками).
- Fake LLM возвращает заранее подготовленный JSON по схеме; fake адаптер умеет «падать» N раз — проверка бэккоффа и статусов.
- Тесты статусов покрывают: валидные/невалидные переходы, обязанность комментария при возврате, запрет смены статуса чужому подрядчику.
- Тесты идемпотентности: повторный OCR одного файла, повторное событие уведомления с тем же ключом.
- Тесты внутреннего бумажного процесса: формирование неизменяемой печатной версии, печать только для inspector/admin, повторная загрузка одного подписанного скана без дубля, связывание скана с правильной печатной версией, запрет перевода `draft_for_signature → new` без проверки скана.
- `pytest -m "not e2e"` в CI локально; e2e — опционально и помечаются маркером.

### Примеры

```python
# tests/test_status_rules.py
def test_return_requires_comment(db, prescription, inspector):
    prescription.status = PrescriptionStatus.FIXED_PENDING_REVIEW
    with pytest.raises(ValueError):
        change_status(db, prescription, PrescriptionStatus.IN_WORK, inspector, comment=None)


def test_contractor_cannot_reject_prescription(db, prescription, contractor_user):
    prescription.status = PrescriptionStatus.IN_WORK
    with pytest.raises(PermissionError):
        change_status(db, prescription, PrescriptionStatus.REJECTED, contractor_user, comment="попытка")
```

```python
# tests/test_file_registry.py
def test_second_run_is_idempotent(tmp_path):
    pdf = make_test_pdf(tmp_path)
    sha = sha256_of(pdf)
    # первый прогон обработал
    mark_processed(db, sha, pdf)
    # повторный прогон
    assert is_already_processed(db, sha) is True
```

### Критерии готовности MVP

MVP готов, если:

- можно создать предписание через веб-форму;
- запись появляется в БД;
- статусная модель и история изменений работают;
- уведомление создаётся в `notifications` и отправляется через Telegram;
- ошибки доставки сохраняются и повторные попытки работают;
- Подрядчик переводит в `in_work` и `fixed_pending_review` — инспектор получает уведомления;
- Инспектор закрывает или возвращает предписание — Подрядчик получает результат;
- OCR-пайплайн принимает сканы, извлекает текст и поля без дублей;
- RAG предлагает нормативные ссылки; инспектор подтверждает их отдельно;
- отчёты строятся из БД;
- повторное создание одного события не создаёт дубль.

---

## Шаг 14. MAX и голосовой ввод (после уточнения API)

### Цель

Подготовить контракты и заглушки для MAX и локального STT. Реальная интеграция выполняется отдельными задачами после получения спецификации API MAX; голосовой ввод — после стабилизации текстового сценария.

### Артефакты

- `src/notifications/adapters/max.py` — реализация по общему контракту адаптера (первоначально заглушка с поднятием `NotImplementedError` и логированием);
- `src/bots/max_bot.py` — заготовка обработчика входящих событий MAX;
- `src/llm/stt/` — интерфейс `transcribe(wav_path) -> text` + заглушка;
- `docs/max_contract.md` — контракт, ожидаемый от API MAX;
- тесты `test_adapters.py`.

### Ключевые решения

- Общий контракт адаптера уже зафиксирован в Шаге 8; добавление MAX не меняет бизнес-логику.
- MAX подключается только при наличии официального API ботов; до этого канал помечается недоступным, используется Telegram.
- Голосовой ввод: аудио → локальный STT (например, whisper.cpp/sherpa-onnx) → текст → LLM-извлечение → обязательная проверка инспектором → регистрация.

### Примеры

```python
# src/notifications/adapters/base.py

class NotificationAdapter(Protocol):
    def send(self, message_text: str, recipient_address: str) -> str: ...
    def send_file(self, recipient_address: str, file_path: str, caption: str | None = None) -> str: ...


class MaxAdapter(NotificationAdapter):
    def __init__(self, api_config): ...

    def send(self, message_text: str, recipient_address: str) -> str:
        raise NotImplementedError("MAX API не подключён: уточнить спецификацию")

    def send_file(self, recipient_address: str, file_path: str, caption: str | None = None) -> str:
        raise NotImplementedError("MAX API не подключён: уточнить спецификацию")
```

Поток голосового ввода:

```text
Аудио
    ↓
Локальный STT
    ↓
Текст
    ↓
LLM-извлечение
    ↓
Проверка инспектором
    ↓
Регистрация
```

Хендлер STT:

```python
def transcribe(wav_path: str) -> str:   # локальный STT
    ...                                 # заглушка до подключения модели
```

```text
Ограничения:
- голосовой ввод не входит в MVP;
- распознавание выполняется локально, документы не покидают сервер;
- результат подлежит проверке инспектором.
```

---

# Приложение A. Вопросы к уточнению и значения по умолчанию

Перед production-реализацией получить ответы:

1. Какой конкретно сервис MAX используется и есть ли официальный API для ботов?
2. Отправлять ли предписание Подрядчику сразу после автоматического OCR или только после проверки инспектором?
3. Кто именно получатель: один ответственный, несколько работников, общий чат подрядчика, личное сообщение?
4. Может ли инспектор быть сотрудником Заказчика, Генподрядчика или обоих?
5. Есть ли у Подрядчика доступ к веб-карточке или достаточно сообщений в мессенджере?
6. Нужно ли подтверждение ознакомления с предписанием (юридически значимое)?
7. Можно ли закрывать предписание без фото/акта?
8. Обязательны ли нормативные ссылки для регистрации каждого предписания?
9. Нужно ли хранить отдельные пункты как самостоятельные объекты контроля?
10. Кто имеет право менять срок устранения?
11. Нужно ли уведомлять руководителя при просрочке?
12. Отправлять ли нормативные рекомендации Подрядчику или только Инспектору?
13. Режим эксплуатации: локальный ПК, локальная сеть или сервер с несколькими пользователями?
14. ОС сервера: Windows 11 или Ubuntu Linux?
15. Какая модель LLM и какой GPU доступны?
16. Нужна ли 2FA для администраторов, смена пароля через email/Telegram, ограничение числа администраторов?
17. Нужна ли временная роль (на период отпуска) и уведомление руководителя о смене ответственного?

Значения по умолчанию (применяются, пока ответ не получен):

- регистрация OCR-документа только после проверки инспектором;
- обязательный получатель — один ответственный Подрядчика;
- обязательный получатель — назначенный Инспектор;
- уведомление через Telegram, если MAX не подключён;
- закрытие — только инспектором;
- возврат — через `fixed_pending_review → in_work`;
- голосовой ввод не входит в MVP;
- нормативная рекомендация требует подтверждения инспектора;
- исходные документы никогда не удаляются;
- подрядчик изменяет только статус и вложения;
- одно предписание = один документ, пункты внутри (`prescription_items`);
- внутреннее предписание считается вручённым Подрядчику только после загрузки и проверки подписанного скана;
- печатная версия внутреннего предписания формируется в PDF, имеет номер версии и не перезаписывается; новая редакция создаёт новую версию;
- наличие и юридическая значимость подписей подтверждаются инспектором вручную; OCR служит для распознавания текста и сверки реквизитов.

---

# Приложение B. Матрица ролей

| Роль | Кто назначает | Где хранится | Может ли пользователь изменить сам |
|---|---|---|---|
| `admin` | Текущий администратор | `users.role` | Нет |
| `inspector` | Администратор | `users.role` | Нет |
| `contractor_responsible` | Администратор (по запросу) | `users.role` + `contractor_id` | Нет |
| `manager` | Администратор (по запросу) | `users.role` + `object_id` (опционально) | Нет |

Правила:

- только администратор создаёт пользователей и назначает роли;
- самостоятельная регистрация не предусмотрена;
- назначение роли через бота без подтверждения администратора запрещено;
- хотя бы один администратор обязателен, рекомендуется 2–3;
- все изменения ролей логируются в `user_role_change_log`;
- деактивация: `is_active = False` (вход запрещён, история предписаний сохраняется).

---

# Приложение C. Манифест артефактов по шагам

| Шаг | Ключевые файлы | Ключевые таблицы/данные |
|---|---|---|
| 1. Окружение | `config.yaml`, `.env.example`, `requirements.txt`, `scripts/pull_models.sh`, `src/config.py` | — |
| 2. Модель данных | `src/db/models.py`, `src/db/engine.py`, Alembic миграции, `docs/schema.sql` | objects, contractors, users, recipient_channels, norm_docs, prescriptions, prescription_items, prescription_norm_refs, prescription_attachments, prescription_history, norm_chunks, norm_chunk_embeddings, notifications, notification_attempts, user_role_change_log, llm_rag_logs, processed_files, system_config |
| 3. Пользователи и роли | `src/users/service.py`, `src/users/admin_routes.py`, `scripts/create_first_admin.py` | users.role, user_role_change_log |
| 4. Внутренние предписания, печать и статусы | `src/prescriptions/*`, `print_service.py`, `signed_scan_service.py` | prescriptions, prescription_attachments, prescription_history |
| 5. OCR-пайплайны | `src/ocr/*`, `scripts/run_ocr_pipeline.py` | processed_files, prescriptions (OCR Заказчика и OCR подписанного внутреннего скана) |
| 6. Локальная LLM | `src/llm/*` | llm_rag_logs |
| 7. RAG | `src/rag/*`, `scripts/ingest_norms.py` | norm_docs, norm_chunks, Chroma `norm_docs` |
| 8. Уведомления | `src/notifications/*`, адаптеры | notifications, notification_attempts, recipient_channels |
| 9. Веб-интерфейс | `src/web/app.py`, `routes/`, `templates/`, `static/` | — (чтение/запись всех таблиц) |
| 10. Telegram-бот | `src/bots/telegram_bot.py`, `handlers.py`, `scripts/run_bot.py` | notifications, prescription_history |
| 11. Отчёты | `src/reports/*`, `scripts/make_report.py` | запросы к prescriptions/notifications/prescription_norm_refs |
| 12. Надёжность | `scripts/backup.*`, `docs/reliability.md` | system_config, бэкапы БД+файлов |
| 13. Тесты/MVP | `tests/*`, `tests/fakes/` | — |
| 14. MAX/STT | `src/notifications/adapters/max.py`, `src/bots/max_bot.py`, `docs/max_contract.md` | recipient_channels (max), заглушки адаптеров |

---

# Порядок запуска шагов

```text
Шаг 1  (окружение)
  ↓
Шаг 2  (БД + миграции) ──→ Шаг 3 (пользователи) ──→ Шаг 9 (веб)
                                                      ↓
Шаг 4  (статусы/создание) ←──────────────────────────┘
  ↓
Шаг 8  (уведомления) ──→ Шаг 10 (Telegram-бот)
  ↓
Шаг 5  (OCR) ──→ Шаг 6 (LLM) ──→ Шаг 7 (RAG) ──→ Шаг 9 (проверка инспектором)
  ↓
Шаг 11 (отчёты)   Шаг 12 (бэкапы)   Шаг 13 (тесты/MVP)   Шаг 14 (MAX/STT)
```

Запуск в эксплуатацию каждого шага — только после прохождения Шага 13 (тесты) и ручной проверки сценария «Критерии готовности MVP».