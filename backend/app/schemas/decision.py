from typing import Self

from pydantic import Field, model_validator

from app.schemas.base import NonEmptyString, SchemaModel
from app.schemas.enums import DecisionStatus, RuleOutcome


class DeterministicRuleResult(SchemaModel):
    """Records outcome of deterministic validation rule"""

    rule_type: NonEmptyString
    outcome: RuleOutcome
    reason: NonEmptyString


class Decision(SchemaModel):
    """Records the evidence-backed result for one requirement"""

    requirement_id: NonEmptyString
    status: DecisionStatus
    
    evidence_ids: list[NonEmptyString] = Field(default_factory=list)
    reason: NonEmptyString
    rule_result: DeterministicRuleResult | None = None

    #validate evidence
    @model_validator(mode="after")
    def require_evidence_for_satisfied_status(self) -> Self:
        if self.status is DecisionStatus.SATISFIED and not self.evidence_ids:
            raise ValueError("a satisfied decision requires at least one evidence ID")
        return self
