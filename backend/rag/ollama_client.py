"""Тонкий клиент к локальному Ollama: эмбеддинги + чат.

Всё, что уходит наружу из RAG-контура, ограничено локальным Ollama — это
реализация требования §4.2 концепта о закрытом контуре для чувствительного
корпуса (внутренние отчёты, патенты не покидают периметр).
"""

from __future__ import annotations

import requests

from ..config import get_settings


class OllamaError(RuntimeError):
    pass


def _base_url() -> str:
    return get_settings().ollama_base_url.rstrip("/")


def embed(texts: list[str]) -> list[list[float]]:
    """Векторизует список текстов одним батч-запросом к Ollama."""
    if not texts:
        return []
    settings = get_settings()
    try:
        response = requests.post(
            f"{_base_url()}/api/embed",
            json={"model": settings.ollama_embed_model, "input": texts},
            timeout=settings.ollama_timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - сетевой сбой
        raise OllamaError(f"Ollama embed failed: {exc}") from exc

    payload = response.json()
    embeddings = payload.get("embeddings")
    if not embeddings:
        single = payload.get("embedding")
        embeddings = [single] if single else []
    if len(embeddings) != len(texts):
        raise OllamaError(
            f"Ollama returned {len(embeddings)} embeddings for {len(texts)} texts"
        )
    return embeddings


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def chat(messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
    """Генерация ответа чат-моделью Ollama (без стрима)."""
    settings = get_settings()
    try:
        response = requests.post(
            f"{_base_url()}/api/chat",
            json={
                "model": settings.ollama_chat_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature},
            },
            timeout=settings.ollama_timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - сетевой сбой
        raise OllamaError(f"Ollama chat failed: {exc}") from exc

    return response.json().get("message", {}).get("content", "").strip()


def health() -> dict:
    """Проверка доступности Ollama и наличия нужных моделей."""
    settings = get_settings()
    try:
        response = requests.get(f"{_base_url()}/api/tags", timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        return {"reachable": False, "error": str(exc)}

    names = {model["name"] for model in response.json().get("models", [])}
    return {
        "reachable": True,
        "base_url": settings.ollama_base_url,
        "embed_model": settings.ollama_embed_model,
        "chat_model": settings.ollama_chat_model,
        "embed_model_present": settings.ollama_embed_model in names,
        "chat_model_present": settings.ollama_chat_model in names,
    }
