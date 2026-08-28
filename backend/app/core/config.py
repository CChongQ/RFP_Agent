from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Evidence-First RFP Qualification Agent"
    app_env: str = "development"
    log_level: str = "INFO"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    database_url: str

    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_embedding_model: str | None = None
    openai_max_retries: int = 2
    openai_timeout_seconds: float = 60

    data_root: Path = Path("./data")
    tender_raw_dir: Path = Path("./data/tenders/raw")
    tender_derived_dir: Path = Path("./data/tenders/derived")

    max_pdf_mb: int = 25
    max_pdf_pages: int = 250

    retrieval_top_k: int = 5
    min_retrieval_score: float = 0.0

    enable_external_api_calls: bool = False
    max_analysis_cost_usd: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]