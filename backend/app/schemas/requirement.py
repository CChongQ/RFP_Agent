from typing import Annotated

from pydantic import Field

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import RequirementType


class Requirement(SchemaModel):
    """Records one traceable requirement extracted from a tender"""

    requirement_id: NonEmptyString
    tender_id: NonEmptyString

    # Keep original wording for audit and normalized wording for retrieval
    requirement_text: NonEmptyString
    normalized_requirement: NonEmptyString

    requirement_type: RequirementType
    # Require both page and excerpt so every requirement remains traceable
    source_page: Annotated[int, Field(ge=1)]
    source_excerpt: NonEmptyString
    requires_human_review: bool = False
