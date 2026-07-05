"""Клиент к Yandex Cloud Foundation Models: text embeddings + YandexGPT.

Заменяет локальный Ollama. RAG-контур теперь ходит в Yandex Cloud:
эмбеддинги считает text-search-doc/query, ответы генерирует YandexGPT.
Ключ и folder-id берутся из окружения (YANDEX_API_KEY / YANDEX_FOLDER_ID).
"""

from __future__ import annotations

import time

import requests

from ..config import get_settings

_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
_EMBED_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding"
_RETRY_STATUS = {429, 500, 502, 503, 504}


class YandexError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.yandex_api_key or not settings.yandex_folder_id:
        raise YandexError("YANDEX_API_KEY и YANDEX_FOLDER_ID не заданы в окружении")
    return {
        "Authorization": f"Api-Key {settings.yandex_api_key}",
        "x-folder-id": settings.yandex_folder_id,
    }


def _post(url: str, payload: dict, retries: int = 4) -> dict:
    settings = get_settings()
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.post(
                url, headers=_headers(), json=payload, timeout=settings.yandex_timeout
            )
            if response.status_code in _RETRY_STATUS:
                last_exc = YandexError(f"HTTP {response.status_code}: {response.text[:200]}")
                time.sleep(1.5 * (attempt + 1))  # бэкофф при 429/5xx
                continue
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(1.0 * (attempt + 1))
    raise YandexError(f"Yandex API недоступен после {retries} попыток: {last_exc}")


def _embed_model_uri(kind: str) -> str:
    settings = get_settings()
    name = settings.yandex_embed_doc_model if kind == "doc" else settings.yandex_embed_query_model
    return f"emb://{settings.yandex_folder_id}/{name}/latest"


def embed(texts: list[str], *, kind: str = "doc") -> list[list[float]]:
    """Векторизует тексты через Yandex.

    Endpoint принимает один текст за запрос, поэтому идём последовательно.
    ``kind='doc'`` для фрагментов корпуса, ``kind='query'`` для запросов пользователя.
    """
    if not texts:
        return []
    uri = _embed_model_uri(kind)
    vectors: list[list[float]] = []
    for text in texts:
        data = _post(_EMBED_URL, {"modelUri": uri, "text": text})
        vector = data.get("embedding")
        if not vector:
            raise YandexError("Yandex вернул пустой эмбеддинг")
        vectors.append([float(x) for x in vector])
    return vectors


def embed_one(text: str, *, kind: str = "query") -> list[float]:
    return embed([text], kind=kind)[0]


def chat(messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """Генерация ответа YandexGPT. messages — [{role, content}] как у OpenAI/Ollama."""
    settings = get_settings()
    body = {
        "modelUri": f"gpt://{settings.yandex_folder_id}/{settings.yandex_chat_model}/latest",
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": str(max_tokens),
        },
        "messages": [{"role": m["role"], "text": m["content"]} for m in messages],
    }
    data = _post(_COMPLETION_URL, body)
    alternatives = data.get("result", {}).get("alternatives", [])
    if not alternatives:
        raise YandexError("Yandex вернул пустой ответ")
    return alternatives[0].get("message", {}).get("text", "").strip()


def health() -> dict:
    """Готовность Yandex-контура. Проверяем наличие ключей (без лишних платных вызовов)."""
    settings = get_settings()
    if not settings.yandex_api_key or not settings.yandex_folder_id:
        return {"reachable": False, "error": "YANDEX_API_KEY / YANDEX_FOLDER_ID не заданы"}
    return {
        "reachable": True,
        "provider": "yandex",
        "embed_model": settings.yandex_embed_doc_model,
        "chat_model": settings.yandex_chat_model,
        "embed_model_present": True,
        "chat_model_present": True,
    }
