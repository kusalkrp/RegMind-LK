import logging
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    postgres_url: str

    # Vector DB
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "sltda_documents"
    qdrant_staging_collection: str = "sltda_documents_next"
    qdrant_exemplars_collection: str = "format_exemplars"

    # AI
    gemini_api_key: str
    gemini_embedding_model: str = "models/text-embedding-004"
    gemini_synthesis_model: str = "gemini-1.5-flash"

    # MCP Server
    mcp_max_concurrency: int = 15
    mcp_tool_timeout_seconds: int = 30

    # RAG
    rag_top_k_chunks: int = 6
    rag_similarity_threshold: float = 0.60
    rag_max_context_tokens: int = 2500
    rag_max_synthesis_tokens: int = 600

    # Ingestion
    ingestion_rate_limit_rps: float = 1.0
    ingestion_min_file_size_kb: int = 5
    ingestion_parse_failure_abort_threshold: float = 0.10
    ingestion_embedding_batch_size: int = 100

    # Observability
    log_level: str = "INFO"
    refresh_notify_slack_webhook: str = ""

    # Storage
    documents_base_path: str = "./documents"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
