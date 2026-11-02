"""Configuration from environment variables (optionally loaded from a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # Slack
    slack_bot_token: str
    slack_app_token: str
    # FAQ source
    faq_source: str  # "auto" | "csv" | "sheets"
    faq_csv_path: Path
    google_sheet_id: str
    google_credentials: str
    faq_sheet_name: str
    unanswered_sheet_name: str
    faq_cache_seconds: int
    # Knowledge base for RAG
    kb_dir: Path
    unanswered_log_path: Path
    # Embeddings: "hashing" (offline default) | "sentence-transformers" | "openai"
    embedding_backend: str
    embedding_model: str
    embedding_base_url: str
    embedding_api_key: str
    # LLM (any OpenAI-compatible /chat/completions endpoint: OpenAI, Ollama, vLLM, LM Studio...)
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    rag_enabled: bool
    rag_top_k: int
    rag_min_score: float
    # Ops
    metrics_port: int
    escalation_contact: str
    log_level: str

    @property
    def llm_configured(self) -> bool:
        # Local endpoints (e.g. Ollama) don't need a key, but they do need a URL + model.
        return bool(self.llm_base_url and self.llm_model)

    @property
    def sheets_configured(self) -> bool:
        return bool(self.google_sheet_id and self.google_credentials)


def load_settings() -> Settings:
    root = PROJECT_ROOT
    return Settings(
        slack_bot_token=_env("SLACK_BOT_TOKEN"),
        slack_app_token=_env("SLACK_APP_TOKEN"),
        faq_source=_env("FAQ_SOURCE", "auto").lower(),
        faq_csv_path=Path(_env("FAQ_CSV_PATH", str(root / "data" / "sample_faq.csv"))),
        google_sheet_id=_env("GOOGLE_SHEET_ID"),
        google_credentials=_env("GOOGLE_APPLICATION_CREDENTIALS"),
        faq_sheet_name=_env("FAQ_SHEET_NAME", "FAQ"),
        unanswered_sheet_name=_env("UNANSWERED_SHEET_NAME", "Unanswered Questions"),
        faq_cache_seconds=_env_int("FAQ_CACHE_SECONDS", 60),
        kb_dir=Path(_env("KB_DIR", str(root / "docs" / "sample_kb"))),
        unanswered_log_path=Path(_env("UNANSWERED_LOG", str(root / "logs" / "unanswered.jsonl"))),
        embedding_backend=_env("EMBEDDING_BACKEND", "hashing").lower(),
        embedding_model=_env("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        embedding_base_url=_env("EMBEDDING_BASE_URL", _env("LLM_BASE_URL")),
        embedding_api_key=_env("EMBEDDING_API_KEY", _env("LLM_API_KEY")),
        llm_base_url=_env("LLM_BASE_URL"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL"),
        llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 30.0),
        rag_enabled=_env("RAG_ENABLED", "true").lower() in ("1", "true", "yes"),
        rag_top_k=_env_int("RAG_TOP_K", 4),
        rag_min_score=_env_float("RAG_MIN_SCORE", 0.25),
        metrics_port=_env_int("METRICS_PORT", 8000),
        escalation_contact=_env("ESCALATION_CONTACT", "the IT team"),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
    )
