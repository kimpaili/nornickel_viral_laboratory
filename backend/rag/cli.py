"""CLI индексации корпуса: python -m backend.rag.cli [путь] [--reindex]

Используется compose-сервисом corpus-index и вручную при локальном запуске.
"""

from __future__ import annotations

import argparse
import sys

from ..db import SessionLocal, init_db
from . import yandex_client
from .indexer import index_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index corpus into pgvector via Yandex Cloud")
    parser.add_argument("path", nargs="?", default=None, help="Путь к папке корпуса")
    parser.add_argument("--reindex", action="store_true", help="Переиндексировать всё")
    args = parser.parse_args(argv)

    health = yandex_client.health()
    if not health.get("reachable"):
        print(f"[!] Yandex недоступен: {health.get('error')}", file=sys.stderr)
        return 2

    init_db()
    with SessionLocal() as session:
        stats = index_corpus(session, args.path, reindex=args.reindex)

    print("Индексация завершена:")
    for key, value in stats.as_dict().items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
