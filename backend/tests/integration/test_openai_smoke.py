import pytest
from openai import OpenAI

from app.core.config import get_settings

"""
Test one real OpenAI request when external calls are enabled.
"""

# Basic tests

@pytest.mark.external
def test_openai_responses_api() -> None:
    settings = get_settings()

    # Safety gate: normal test runs must not make a paid external request 
    if not settings.enable_external_api_calls:
        pytest.skip("External API calls are disabled")

    if settings.openai_api_key is None:
        pytest.skip("OPENAI_API_KEY is not configured")

    api_key = settings.openai_api_key.get_secret_value().strip()

    if not api_key:
        pytest.skip("OPENAI_API_KEY is empty")

    if not settings.openai_model:
        pytest.skip("OPENAI_MODEL is not configured")

    # note: keep the paid smoke request as small as possible
    client = OpenAI(
        api_key=api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )

    response = client.responses.create(
        model=settings.openai_model,
        input="Reply with exactly the word OK.",
    )

    # Confirm the response contains usable text and follows the short instruction
    assert response.output_text
    assert "OK" in response.output_text.upper()
