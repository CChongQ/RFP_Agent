from enum import StrEnum

from pydantic import Field

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import EvidenceType, RequirementType
from app.schemas.rules import RuleOperator


class ExtractedScalarType(StrEnum):
    """Scalar encodings accepted at the model boundary."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


class ExtractedRuleParameterName(StrEnum):
    """Names understood by the rule-candidate conversion layer"""

    MINIMUM = "minimum"
    VALUE_FIELD = "value_field"
    ALLOWED_VALUES = "allowed_values"


class ExtractedEvidenceFilter(SchemaModel):
    """Model-facing equality filter with an explicit scalar encoding."""

    field: NonEmptyString
    value_type: ExtractedScalarType
    value_text: NonEmptyString


class ExtractedRuleParameter(SchemaModel):
    """Model-facing named rule argument; scalar arguments contain one value."""

    name: ExtractedRuleParameterName
    values: list[NonEmptyString] = Field(min_length=1)


class ExtractedRuleCandidate(SchemaModel):
    """Flat rule proposal designed for reliable Structured Outputs."""

    subject: NonEmptyString
    evidence_type: EvidenceType
    filters: list[ExtractedEvidenceFilter]
    operator: RuleOperator
    parameters: list[ExtractedRuleParameter]


class ExtractedRequirementCandidate(SchemaModel):
    """Represents one model-extracted requirement"""

    # note: assign IDs in app code so the model cannot invent IDs
    requirement_text: NonEmptyString
    normalized_requirement: NonEmptyString
    requirement_type: RequirementType
    source_block_ids: list[NonEmptyString] = Field(min_length=1)
    rule_candidates: list[ExtractedRuleCandidate]
    requires_human_review: bool


class RequirementExtractionBatch(SchemaModel):
    """Contains structured requirements returned for one page-aware text chunk"""

    # One batch maps to one page-aware chunk and may contain no obligations
    requirements: list[ExtractedRequirementCandidate]
