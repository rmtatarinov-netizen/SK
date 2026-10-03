"""Смоук-тест Шага 1: конфигурация загружается из config.yaml + .env."""

from src.config import PROJECT_ROOT, get_settings


def test_settings_loaded_from_yaml():
    s = get_settings()
    assert s.app.name == "prescriptions_system"
    assert s.ollama.llm_model == "qwen2.5:7b-instruct"
    assert s.ollama.embed_model == "bge-m3"
    assert s.rag.collection == "norm_docs"
    assert s.rag.top_k == 5
    assert s.ocr.langs == ["ru"]


def test_notification_retry_policy():
    s = get_settings()
    assert s.notifications.max_attempts == 5
    assert s.notifications.retry_delays_s == [60, 300, 900, 3600]


def test_sqlalchemy_url_is_absolute_for_sqlite():
    s = get_settings()
    assert s.sqlalchemy_url.startswith("sqlite:///")
    assert PROJECT_ROOT.as_posix() in s.sqlalchemy_url


def test_data_directories_exist():
    s = get_settings()
    for relative in (
        s.storage.inbox_customer,
        s.storage.processed,
        s.storage.storage,
        s.storage.uploads,
    ):
        assert s.resolve_path(relative).is_dir()
