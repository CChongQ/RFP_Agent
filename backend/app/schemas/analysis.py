from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from app.schemas.base import NonEmptyString, SchemaModel, Sha256
from app.schemas.decision import Decision
from app.schemas.enums import OverallRecommendation
from app.schemas.requirement import Requirement


def _has_duplicates(values: list[str]) -> bool:
    return len(values) != len(set(values))


class CreateAnalysisRequest(SchemaModel):
    tender_id: Literal["TENDER-001"] # to be changed, after MVP


class ToolCallTrace(SchemaModel):
    """Records one validated read-only tool call"""

    requirement_id: NonEmptyString
    tool_name: NonEmptyString
    
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    result_ids: list[NonEmptyString] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)


class TraceMetadata(SchemaModel):
    """Summarizes model usage and runtime context for one analysis"""
    
    document_sha256: Sha256
    model_version: NonEmptyString
    prompt_version: NonEmptyString
    
    latency_ms: Annotated[int, Field(ge=0)]
    
    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0
    estimated_cost_usd: Annotated[float, Field(ge=0)] = 0.0
    
    extracted_requirement_ids: list[NonEmptyString] = Field(default_factory=list)
    requirement_source_block_ids: dict[str, list[NonEmptyString]] = Field(
        default_factory=dict
    )
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    errors: list[NonEmptyString] = Field(default_factory=list)


class AnalysisResult(SchemaModel):
    """Combines requirements, decisions, recommendation, and trace data"""

    analysis_id: NonEmptyString
    tender_id: NonEmptyString
    
    requirements: list[Requirement] = Field(min_length=1)
    decisions: list[Decision] = Field(min_length=1)
    overall_recommendation: OverallRecommendation
    
    risks: list[NonEmptyString] = Field(default_factory=list)
    human_review_reasons: list[NonEmptyString] = Field(default_factory=list)
    trace: TraceMetadata

    @model_validator(mode="after")
    def validate_requirement_decision_links(self) -> Self:
        requirement_ids = [requirement.requirement_id for requirement in self.requirements]
        decision_ids = [decision.requirement_id for decision in self.decisions]

        if any(requirement.tender_id != self.tender_id for requirement in self.requirements):
            raise ValueError("all requirements must belong to the analysis tender")
        
        if _has_duplicates(requirement_ids):
            raise ValueError("requirement IDs must be unique")
        
        if _has_duplicates(decision_ids):
            raise ValueError("each requirement can have only one decision")
        if set(requirement_ids) != set(decision_ids):
            raise ValueError("every requirement must have exactly one decision")
        
        return self
