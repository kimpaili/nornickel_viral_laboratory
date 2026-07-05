"""Поиск по корпусу (pgvector, косинусная близость) и генерация ответа
с обязательным цитированием источников.

Принцип концепта: LLM оборачивает найденное в текст со ссылками, но не
изобретает факты и не считает числа эффектов — это делает детерминированный
движок (backend/engine). RAG отвечает за обоснования и литературную опору.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .. import models
from ..config import get_settings
from . import yandex_client

_SYSTEM_PROMPT = (
    "Ты — помощник по обогащению руд. Отвечай СТРОГО по предоставленным фрагментам "
    "корпуса. Не выдумывай факты и не приводи числовые оценки эффекта — их считает "
    "отдельный детерминированный движок, не ты. После каждого утверждения ставь ссылку "
    "на источник в виде [номер]. Если ответа в корпусе нет — честно скажи: "
    "«В корпусе не найдено». Пиши по-русски, кратко и по делу."
)


@dataclass
class Hit:
    chunk_id: int
    source_file: str
    page: int | None
    plant_hint: str | None
    content: str
    distance: float

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "source_file": self.source_file,
            "page": self.page,
            "plant_hint": self.plant_hint,
            "snippet": self.content[:400],
            "distance": round(self.distance, 4),
        }


def search(
    session: Session,
    query: str,
    *,
    k: int | None = None,
    plant_hint: str | None = None,
) -> list[Hit]:
    top_k = k or get_settings().rag_top_k
    query_vector = yandex_client.embed_one(query, kind="query")
    distance = models.CorpusChunk.embedding.cosine_distance(query_vector)

    stmt = session.query(models.CorpusChunk, distance.label("distance"))
    if plant_hint:
        stmt = stmt.filter(models.CorpusChunk.plant_hint == plant_hint)
    rows = stmt.order_by(distance).limit(top_k).all()

    return [
        Hit(
            chunk_id=chunk.id,
            source_file=chunk.source_file,
            page=chunk.page,
            plant_hint=chunk.plant_hint,
            content=chunk.content,
            distance=float(dist),
        )
        for chunk, dist in rows
    ]


def answer(
    session: Session,
    query: str,
    *,
    k: int | None = None,
    plant_hint: str | None = None,
) -> dict:
    hits = search(session, query, k=k, plant_hint=plant_hint)
    if not hits:
        return {
            "answer": "В корпусе не найдено релевантных фрагментов.",
            "citations": [],
            "used_llm": False,
        }

    context = "\n\n".join(_format_hit(index, hit) for index, hit in enumerate(hits, 1))
    try:
        text = yandex_client.chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Фрагменты корпуса:\n{context}\n\nВопрос: {query}",
                },
            ]
        )
        used_llm = True
    except yandex_client.YandexError as exc:
        text = _fallback(hits, str(exc))
        used_llm = False

    return {
        "answer": text,
        "citations": [{"n": index, **hit.as_dict()} for index, hit in enumerate(hits, 1)],
        "used_llm": used_llm,
    }


def _format_hit(index: int, hit: Hit) -> str:
    where = hit.source_file + (f", стр. {hit.page}" if hit.page else "")
    return f"[{index}] Источник: {where}\n{hit.content}"


def _fallback(hits: list[Hit], reason: str) -> str:
    lines = ["Yandex LLM недоступен — выдаю найденные фрагменты без генерации:", ""]
    for index, hit in enumerate(hits, 1):
        where = hit.source_file + (f", стр. {hit.page}" if hit.page else "")
        lines.append(f"[{index}] {where}: {hit.content[:200]}…")
    lines.append("")
    lines.append(f"(LLM fallback: {reason})")
    return "\n".join(lines)
