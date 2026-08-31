from app.prompts.loader import PromptDefinition, PromptLoadError, load_prompt

REQUIREMENT_EXTRACTION_PROMPT = load_prompt("requirement_extraction.yaml")
EVIDENCE_ASSESSMENT_PROMPT = load_prompt("evidence_assessment.yaml")
ANALYSIS_PROMPT_VERSION = "+".join(
    (
        REQUIREMENT_EXTRACTION_PROMPT.identifier,
        EVIDENCE_ASSESSMENT_PROMPT.identifier,
    )
)

__all__ = [
    "ANALYSIS_PROMPT_VERSION",
    "EVIDENCE_ASSESSMENT_PROMPT",
    "PromptDefinition",
    "PromptLoadError",
    "REQUIREMENT_EXTRACTION_PROMPT",
    "load_prompt",
]
