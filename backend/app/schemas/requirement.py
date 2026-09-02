from typing import Annotated, Self

from pydantic import Field, model_validator

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import RequirementType
from app.schemas.rules import RuleSpec


class SourceReference(SchemaModel):
    """Point to 1 source block in the tender PDF"""

    block_id: NonEmptyString
    page_number: Annotated[int, Field(ge=1)]
    bounding_box: tuple[float, float, float, float]


class Requirement(SchemaModel):
    """Records one traceable requirement extracted from a tender"""

    requirement_id: NonEmptyString
    tender_id: NonEmptyString

    # Keep original wording for audit and normalized wording for retrieval
    requirement_text: NonEmptyString
    normalized_requirement: NonEmptyString

    requirement_type: RequirementType
    # determinstic get these values from validated source blocks
    source_page: Annotated[int, Field(ge=1)]
    source_excerpt: NonEmptyString
    source_references: list[SourceReference] = Field(min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)
    requires_human_review: bool = False

    @model_validator(mode="after")
    def validate_source_page(self) -> Self:
        if self.source_page != self.source_references[0].page_number:
            raise ValueError("source_page must match the first source reference")
        return self
