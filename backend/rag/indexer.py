"""Индексация корпуса в pgvector. Идемпотентна по хэшу файла:
неизменённые файлы пропускаются, изменённые переиндексируются целиком.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from ..config import get_settings
from . import loader, yandex_client
from .chunk import chunk_text

_EMBED_BATCH = 32


@dataclass
class IndexStats:
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    chunks_added: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "files_seen": self.files_seen,
            "files_indexed": self.files_indexed,
            "files_skipped": self.files_skipped,
            "chunks_added": self.chunks_added,
            "errors": self.errors,
        }


def index_corpus(
    session: Session,
    root: str | Path | None = None,
    *,
    reindex: bool = False,
) -> IndexStats:
    root_path = Path(root) if root is not None else Path(get_settings().corpus_path)
    stats = IndexStats()

    for path in loader.iter_files(root_path):
        stats.files_seen += 1
        rel = _relative(path, root_path)
        try:
            added = _index_file(session, path, rel, reindex=reindex)
        except Exception as exc:  # noqa: BLE001 - один файл не должен валить всю индексацию
            session.rollback()
            stats.errors.append(f"{rel}: {exc}")
            continue

        if added is None:
            stats.files_skipped += 1
        else:
            stats.files_indexed += 1
            stats.chunks_added += added
        session.commit()

    return stats


def _index_file(
    session: Session,
    path: Path,
    rel: str,
    *,
    reindex: bool,
) -> int | None:
    file_hash = _hash_file(path)
    document = (
        session.query(models.CorpusDocument).filter_by(source_file=rel).one_or_none()
    )
    if document and document.file_hash == file_hash and not reindex:
        return None

    plant_hint = loader.detect_plant_hint(path)
    kind = loader.kind_of(path)

    if document:
        session.query(models.CorpusChunk).filter_by(document_id=document.id).delete()
        document.file_hash = file_hash
        document.kind = kind
        document.plant_hint = plant_hint
    else:
        document = models.CorpusDocument(
            source_file=rel,
            kind=kind,
            file_hash=file_hash,
            plant_hint=plant_hint,
            n_chunks=0,
        )
        session.add(document)
        session.flush()

    pieces: list[tuple[int | None, str]] = []
    for unit in loader.load_units(path):
        for chunk in chunk_text(unit.text):
            pieces.append((unit.page, chunk))

    for batch_start in range(0, len(pieces), _EMBED_BATCH):
        batch = pieces[batch_start : batch_start + _EMBED_BATCH]
        vectors = yandex_client.embed([text for _, text in batch], kind="doc")
        for offset, ((page, text), vector) in enumerate(zip(batch, vectors)):
            session.add(
                models.CorpusChunk(
                    document_id=document.id,
                    source_file=rel,
                    page=page,
                    chunk_index=batch_start + offset,
                    content=text,
                    plant_hint=plant_hint,
                    embedding=vector,
                )
            )

    document.n_chunks = len(pieces)
    return len(pieces)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
