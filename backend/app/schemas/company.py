from pydantic import Field

from app.schemas.base import SchemaModel
from app.schemas.evidence import Evidence


class CompanyEvidenceSeed(SchemaModel):
    """Defines the company evidence data loaded from a JSON file"""

    #must have at least 1 evidence, empty seed rejected
    evidence: list[Evidence] = Field(min_length=1)
