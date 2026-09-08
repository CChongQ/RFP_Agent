import pytest
from pydantic import ValidationError

from app.prompts import (
    ANALYSIS_PROMPT_VERSION,
    EVIDENCE_ASSESSMENT_PROMPT,
    REQUIREMENT_EXTRACTION_PROMPT,
)
from app.prompts.loader import PromptDefinition, PromptLoadError, load_prompt

"""
Test prompt loading, versions, and safe file names.
"""

# Basic tests


def test_packaged_prompts_compose_traceable_version() -> None:
    assert ANALYSIS_PROMPT_VERSION == (
        f"{REQUIREMENT_EXTRACTION_PROMPT.identifier}+"
        f"{EVIDENCE_ASSESSMENT_PROMPT.identifier}"
    )


# Corner-case tests
def test_prompt_definition_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PromptDefinition.model_validate(
            {
                "name": "example-prompt",
                "version": 1,
                "instructions": "Do the task",
                "unexpected": "not allowed",
            }
        )


def test_prompt_loader_rejects_paths_outside_prompt_package() -> None:
    with pytest.raises(PromptLoadError, match="invalid prompt filename"):
        load_prompt("../secret.yaml")
