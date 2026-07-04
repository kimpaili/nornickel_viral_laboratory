"""Разбиение текста на перекрывающиеся фрагменты по границам абзацев."""

from __future__ import annotations


def chunk_text(text: str, *, size: int = 1000, overlap: int = 150) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    paragraphs = [para.strip() for para in text.split("\n") if para.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        # Слишком длинный абзац режем жёстко по символам с перекрытием.
        if len(paragraph) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(paragraph, size=size, overlap=overlap))
            continue

        if not current:
            current = paragraph
        elif len(current) + 1 + len(paragraph) <= size:
            current = f"{current}\n{paragraph}"
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n{paragraph}".strip() if tail else paragraph

    if current:
        chunks.append(current)
    return chunks


def _hard_split(text: str, *, size: int, overlap: int) -> list[str]:
    step = max(size - overlap, 1)
    return [text[start : start + size] for start in range(0, len(text), step)]
