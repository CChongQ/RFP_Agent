from pydantic import Field

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import RequirementType


class ExtractedRequirementCandidate(SchemaModel):
    """Represents one model-extracted requirement"""

    # note: assign IDs in app code so the model cannot invent IDs
    requirement_text: NonEmptyString
    normalized_requirement: NonEmptyString
    requirement_type: RequirementType
    source_page: int = Field(ge=1)
    source_excerpt: NonEmptyString
    requires_human_review: bool = False


class RequirementExtractionBatch(SchemaModel):
    """Contains structured requirements returned for one page-aware text chunk"""

    # One batch maps to one page-aware chunk and may contain no obligations
    requirements: list[ExtractedRequirementCandidate] = Field(default_factory=list)
