from datetime import date
from typing import Self

from pydantic import JsonValue, model_validator

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import EvidenceType


class Evidence(SchemaModel):
    """Defines one company record used to check a tender requirement"""

    evidence_id: NonEmptyString
    
    evidence_type: EvidenceType
    supporting_text: NonEmptyString | None = None
    structured_value: JsonValue | None = None
    
    valid_from: date | None = None
    valid_until: date | None = None

    @model_validator(mode="after")
    def validate_content_and_dates(self) -> Self:
        
        #Every evidence record needs content that can support or test a claim
        if self.supporting_text is None and self.structured_value is None:
            raise ValueError("evidence requires supporting_text or structured_value")
        
        #Reject impossible validity windows before deterministic checks run
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("valid_until cannot be earlier than valid_from")
        return self
