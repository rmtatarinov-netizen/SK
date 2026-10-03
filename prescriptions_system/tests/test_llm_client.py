"""Тесты Ollama-клиента (Шаг 6): retry на битом JSON, fallback, embed. Сеть не используется."""

from __future__ import annotations

import pytest
import requests

import src.llm.client as llm_client_module
from src.llm.client import LLMError, OllamaClient


class FakeResponse:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self._content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return {"message": {"content": self._content}}


def _client() -> OllamaClient:
    return OllamaClient(base_url="http://fake.local", model="fake-model", timeout_s=1)


def test_chat_json_retries_once_then_succeeds(monkeypatch):
    seen_prompts: list[str] = []

    def fake_post(url, json=None, timeout=None):
        seen_prompts.append(json["messages"][0]["content"])
        assert url.endswith("/api/chat")
        if len(seen_prompts) == 1:
            return FakeResponse("это не json")
        return FakeResponse('{"confidence": 0.4}')

    monkeypatch.setattr(llm_client_module.requests, "post", fake_post)
    res = _client().chat_json("ПРИМПИТ")
    assert res.ok is True
    assert res.data == {"confidence": 0.4}
    assert len(seen_prompts) == 2
    assert "JSON" in seen_prompts[1]  # повтор с уточнением формата


def test_chat_json_gives_up_after_attempts(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(1)
        return FakeResponse("bad json")

    monkeypatch.setattr(llm_client_module.requests, "post", fake_post)
    client = _client()
    res = client.chat_json("P")
    assert res.ok is False
    assert "ответ не JSON" in res.error
    assert len(calls) == client.max_attempts


def test_chat_json_network_error(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise requests.ConnectionError("ollama down")

    monkeypatch.setattr(llm_client_module.requests, "post", fake_post)
    res = _client().chat_json("P")
    assert res.ok is False
    assert "ConnectionError" in res.error


def test_chat_json_requires_json_object(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return FakeResponse("[1, 2, 3]")

    monkeypatch.setattr(llm_client_module.requests, "post", fake_post)
    res = _client().chat_json("P")
    assert res.ok is False
    assert "JSON-объект" in res.error


def test_embed_ok(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/api/embeddings")

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"embedding": [0.1, 0.2, 0.3]}

        return R()

    monkeypatch.setattr(llm_client_module.requests, "post", fake_post)
    assert _client().embed("текст") == [0.1, 0.2, 0.3]


def test_embed_empty_raises(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"embedding": []}

        return R()

    monkeypatch.setattr(llm_client_module.requests, "post", fake_post)
    with pytest.raises(LLMError):
        _client().embed("текст")
