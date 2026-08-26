from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"

    database_url: str
    redis_url: str
    qdrant_url: str
    qdrant_collection_name: str = "techpilot_chunks"

    auth_secret_key: str = "dev-only-change-me"
    auth_access_token_minutes: int = 60
    auth_demo_enabled: bool = True

    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_dimension: int = 768
    embedding_batch_size: int = 32

    deepseek_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = 60.0
    answer_context_max_characters: int = 12_000

    bm25_k1: float = 1.5
    bm25_b: float = 0.75

    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_batch_size: int = 8
    reranker_max_length: int = 512
    answer_retrieval_candidate_limit: int = 40
    answer_rerank_depth: int = 20
    answer_rrf_k: int = 60

    @model_validator(mode="after")
    def validate_auth_settings(self) -> "Settings":
        if self.auth_access_token_minutes <= 0:
            raise ValueError("auth_access_token_minutes must be positive")
        if not self.auth_secret_key.strip():
            raise ValueError("auth_secret_key must not be empty")
        if (
            self.app_env.lower() == "production"
            and self.auth_secret_key == "dev-only-change-me"
        ):
            raise ValueError(
                "AUTH_SECRET_KEY must be changed before production startup"
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
