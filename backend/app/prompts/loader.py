from functools import lru_cache
from importlib.resources import files
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class PromptLoadError(RuntimeError):
    """when a prompt file cannot be loaded safely"""


class PromptDefinition(BaseModel):
    """Validated prompt text and the version saved with each analysis"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    version: int = Field(ge=1, strict=True)
    instructions: str

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        # Reject blank prompts before an analysis can make a paid API call
        instructions = value.strip()
        if not instructions:
            raise ValueError("instructions cannot be empty")
        return instructions

    @property
    def identifier(self) -> str:
        """for analysis trace data"""

        return f"{self.name}-v{self.version}"


@lru_cache
def load_prompt(filename: str) -> PromptDefinition:
    """Load and validate one YAML prompt packaged with the application"""

    # accept only a file name so callers cannot read outside this package
    if Path(filename).name != filename or not filename.endswith(".yaml"):
        raise PromptLoadError(f"invalid prompt filename: {filename}")

    try:
        prompt_text = files("app.prompts").joinpath(filename).read_text(encoding="utf-8")
        
        # Safe loading prevents YAML from creating arbitrary Python objects
        payload = yaml.safe_load(prompt_text)
        
        return PromptDefinition.model_validate(payload)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise PromptLoadError(f"invalid prompt file: {filename}") from exc
