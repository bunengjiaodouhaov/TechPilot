from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"

    database_url: str
    redis_url: str
    qdrant_url: str
    qdrant_collection_name: str = "techpilot_chunks"

    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_dimension: int = 768
    embedding_batch_size: int = 32

    deepseek_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = 60.0
    answer_context_max_characters: int = 12_000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()