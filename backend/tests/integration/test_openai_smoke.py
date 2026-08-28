"""Check external AI flow: pass safety gates, call Responses once, and validate its text."""

import pytest
from openai import OpenAI

from app.core.config import get_settings


@pytest.mark.external
def test_openai_responses_api() -> None:
    settings = get_settings()

    # Safety gate: normal test runs must not make a paid external request accidentally
    if not settings.enable_external_api_calls:
        pytest.skip("External API calls are disabled")

    if settings.openai_api_key is None:
        pytest.skip("OPENAI_API_KEY is not configured")

    api_key = settings.openai_api_key.get_secret_value().strip()

    if not api_key:
        pytest.skip("OPENAI_API_KEY is empty")

    if not settings.openai_model:
        pytest.skip("OPENAI_MODEL is not configured")

    # make one minimal Responses API request using configured credentials and limits
    client = OpenAI(
        api_key=api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )

    response = client.responses.create(
        model=settings.openai_model,
        input="Reply with exactly the word OK.",
    )

    # API returned usable text and followed the simple smoke-test instruction
    assert response.output_text
    assert "OK" in response.output_text.upper()
