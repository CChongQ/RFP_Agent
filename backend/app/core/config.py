from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_APP_NAME = "Evidence-First RFP Qualification Agent"
APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    # Read local env values, ignore unrelated settings
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = DEFAULT_APP_NAME
    app_env: str = "development"
    log_level: str = "INFO"
    analysis_progress_log_path: Path = Path("./logs/analysis_progress.jsonl")
    analysis_run_output_dir: Path = Path("./data/evaluation/runs")

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65_535)

    # Require an explicit database target so data is never written accidentally
    database_url: str

    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_embedding_model: str | None = None
    openai_max_retries: int = Field(default=2, ge=0)
    openai_timeout_seconds: float = Field(default=60, gt=0)

    data_root: Path = Path("./data")
    tender_manifest_path: Path = Path("./data/tenders/manifest.csv")
    tender_raw_dir: Path = Path("./data/tenders/raw")
    tender_derived_dir: Path = Path("./data/tenders/derived")

    max_pdf_mb: int = Field(default=25, ge=1)
    max_pdf_pages: int = Field(default=250, ge=1)
    max_requirement_chunk_characters: int = Field(default=12_000, ge=1)
    analysis_page_threshold: int = Field(default=50, ge=1)

    retrieval_top_k: int = Field(default=5, ge=1, le=50)
    min_retrieval_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    evidence_embedding_batch_size: int = Field(default=100, ge=1)

    # Require an explicit opt-in before tests can make paid external calls
    enable_external_api_calls: bool = False
    max_analysis_cost_usd: float = Field(default=1.0, ge=0)


@lru_cache
def get_settings() -> Settings:
    # Reuse one validated settings object during the application process
    return Settings()  # type: ignore[call-arg]
