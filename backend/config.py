from dataclasses import dataclass
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
        "Factory of Hypotheses MVP",
    )
    openrouter_app_url: str | None = os.getenv("OPENROUTER_APP_URL")

    # RAG через Ollama (локальный контур для корпуса, §4.2 концепта)
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "granite-embedding:278m")
    ollama_chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:3b")
    ollama_timeout: float = float(os.getenv("OLLAMA_TIMEOUT", "180"))
    embed_dim: int = int(os.getenv("EMBED_DIM", "768"))
    corpus_path: str = os.getenv("CORPUS_PATH", "Задача 1")
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "6"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
