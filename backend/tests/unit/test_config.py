"""Check config flow: parse typed environment values, require DB config, and mask secrets."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_load_typed_values(monkeypatch: pytest.MonkeyPatch) -> None:
    # provide isolated temp string environment values without reading the local .env file
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:password@localhost:5432/test_db",
    )
    monkeypatch.setenv("MAX_PDF_PAGES", "100")
    monkeypatch.setenv("ENABLE_EXTERNAL_API_CALLS", "false")

    settings = Settings(_env_file=None)

    # int and bool values were converted correctly.
    assert settings.max_pdf_pages == 100
    assert settings.enable_external_api_calls is False


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    # Remove the required database setting and confirm validation fails immediately
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_openai_key_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    # Load a fake key, then confirm it remains accessible to code but hidden in representations.
    test_key = "test-key-that-must-not-appear"

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:password@localhost:5432/test_db",
    )
    monkeypatch.setenv("OPENAI_API_KEY", test_key)

    settings = Settings(_env_file=None)

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == test_key
    assert test_key not in repr(settings)
    assert test_key not in str(settings.openai_api_key)
