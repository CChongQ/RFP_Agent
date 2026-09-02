from typing import Annotated

from pydantic import Field

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import EvidenceType


class EvidenceSearchHit(SchemaModel):
    """Returns one ranked company-evidence match"""

    evidence_id: NonEmptyString
    evidence_type: EvidenceType
    supporting_excerpt: NonEmptyString
    score: Annotated[float, Field(ge=-1.0, le=1.0)]
