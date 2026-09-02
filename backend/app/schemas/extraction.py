from pydantic import Field

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import RequirementType
from app.schemas.rules import RuleCandidate


class ExtractedRequirementCandidate(SchemaModel):
    """Represents one model-extracted requirement"""

    # note: assign IDs in app code so the model cannot invent IDs
    requirement_text: NonEmptyString
    normalized_requirement: NonEmptyString
    requirement_type: RequirementType
    source_block_ids: list[NonEmptyString] = Field(min_length=1)
    rule_candidates: list[RuleCandidate] = Field(default_factory=list)
    requires_human_review: bool = False


class RequirementExtractionBatch(SchemaModel):
    """Contains structured requirements returned for one page-aware text chunk"""

    # One batch maps to one page-aware chunk and may contain no obligations
    requirements: list[ExtractedRequirementCandidate] = Field(default_factory=list)
