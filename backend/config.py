from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
import os

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5433/factory_hypotheses",
    )
    openrouter_api_key: str | None = os.getenv("OPENROUTER_API_KEY")
    openrouter_base_url: str = os.getenv(
        "OPENROUTER_BASE_URL",
        "https://openrouter.ai/api/v1",
    )
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "~openai/gpt-latest")
    openrouter_app_title: str = os.getenv(
        "OPENROUTER_APP_TITLE",
        "Лаборатория гипотез MVP",
    )
    openrouter_app_url: str | None = os.getenv("OPENROUTER_APP_URL")

    # RAG через Yandex Cloud Foundation Models (YandexGPT + text embeddings)
    yandex_api_key: str | None = os.getenv("YANDEX_API_KEY")
    yandex_folder_id: str | None = os.getenv("YANDEX_FOLDER_ID")
    yandex_chat_model: str = os.getenv("YANDEX_CHAT_MODEL", "yandexgpt-lite")
    yandex_embed_doc_model: str = os.getenv("YANDEX_EMBED_DOC_MODEL", "text-search-doc")
    yandex_embed_query_model: str = os.getenv("YANDEX_EMBED_QUERY_MODEL", "text-search-query")
    yandex_timeout: float = float(os.getenv("YANDEX_TIMEOUT", "60"))
    # Эмбеддинги Yandex — 256 измерений (у Ollama было 768). При смене провайдера
    # корпус нужно переиндексировать: размерность вектор-колонки должна совпадать.
    embed_dim: int = int(os.getenv("EMBED_DIM", "256"))
    corpus_path: str = os.getenv("CORPUS_PATH", "Задача 1")
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "6"))

    score_weight_usd: Decimal = Decimal(os.getenv("SCORE_WEIGHT_USD", "45"))
    score_weight_tons: Decimal = Decimal(os.getenv("SCORE_WEIGHT_TONS", "20"))
    score_weight_probability: Decimal = Decimal(os.getenv("SCORE_WEIGHT_PROBABILITY", "15"))
    score_weight_coverage: Decimal = Decimal(os.getenv("SCORE_WEIGHT_COVERAGE", "15"))
    score_weight_feasible: Decimal = Decimal(os.getenv("SCORE_WEIGHT_FEASIBLE", "10"))
    score_weight_risk: Decimal = Decimal(os.getenv("SCORE_WEIGHT_RISK", "10"))
    score_weight_dead_end: Decimal = Decimal(os.getenv("SCORE_WEIGHT_DEAD_END", "60"))
    score_weight_conflict: Decimal = Decimal(os.getenv("SCORE_WEIGHT_CONFLICT", "8"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
