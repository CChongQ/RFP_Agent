"""Validate generic deterministic-rule data before runtime use."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StrictInt, field_validator

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import EvidenceType


type RuleFilterValue = str | int | float | bool


class RuleOperator(StrEnum):
    """supported deterministic checks"""

    MINIMUM_COUNT = "minimum_count"
    MINIMUM_VALUE = "minimum_value"
    ALLOWED_VALUE = "allowed_value"
    VALID_UNTIL = "valid_until"
    CERTIFICATION_VALIDITY = "certification_validity"


class EvidenceFilter(SchemaModel):
    """Match one evidence field to an expected value"""

    field: NonEmptyString
    equals: RuleFilterValue


class EvidenceSelector(SchemaModel):
    """Describes which stored evidence records a rule needs."""

    evidence_type: EvidenceType
    filters: list[EvidenceFilter] = Field(default_factory=list)


class MinimumCountCheck(SchemaModel):
    """Checks the number of matching records meets a minimum"""

    operator: Literal[RuleOperator.MINIMUM_COUNT]
    minimum: Annotated[StrictInt, Field(ge=0)]


class MinimumValueCheck(SchemaModel):
    """Checks a numeric value meets a minimum."""

    operator: Literal[RuleOperator.MINIMUM_VALUE]
    value_field: NonEmptyString
    minimum: Annotated[Decimal, Field(allow_inf_nan=False)]

    @field_validator("minimum", mode="before")
    @classmethod
    def reject_boolean_minimum(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("minimum must be numeric, not boolean")
        return value


class AllowedValueCheck(SchemaModel):
    """Checks a value appears in an allow-list"""

    operator: Literal[RuleOperator.ALLOWED_VALUE]
    value_field: NonEmptyString
    allowed_values: list[NonEmptyString] = Field(min_length=1)


class ValidUntilCheck(SchemaModel):
    """Checks evidence expiry against the analysis date"""

    operator: Literal[RuleOperator.VALID_UNTIL]


class CertificationValidityCheck(SchemaModel):
    """Checks certification status and expiry"""

    operator: Literal[RuleOperator.CERTIFICATION_VALIDITY]


# The operator lets Pydantic choose and validate the matching check model.
type RuleCheck = Annotated[
    MinimumCountCheck
    | MinimumValueCheck
    | AllowedValueCheck
    | ValidUntilCheck
    | CertificationValidityCheck,
    Field(discriminator="operator"),
]


class RuleCandidate(SchemaModel):
    """Represents a check rule proposed by model during requirement extraction"""

    subject: NonEmptyString
    evidence_selector: EvidenceSelector
    check: RuleCheck


class RuleSpec(SchemaModel):
    """Combines a check with the evidence it should inspect"""

    rule_id: NonEmptyString
    subject: NonEmptyString
    evidence_selector: EvidenceSelector
    check: RuleCheck
