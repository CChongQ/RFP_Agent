from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

type NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

type Sha256 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Fa-f0-9]{64}$"),
]


class SchemaModel(BaseModel):
    """Rejects unexpected fields in data exchanged by the workflow"""

    # Reject typos instead of silently dropping fields at service boundaries
    model_config = ConfigDict(extra="forbid")
