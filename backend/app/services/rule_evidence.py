"""Read the exact stored evidence value required by a deterministic rule."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.database.models import EvidenceRecord
from app.schemas.enums import EvidenceType
from app.schemas.rules import (
    AllowedValueCheck,
    CertificationValidityCheck,
    EvidenceFilter,
    EvidenceSelector,
    MinimumCountCheck,
    MinimumValueCheck,
    RuleSpec,
    ValidUntilCheck,
)

# ========== Evidence value and approved fields ==========

type RuleEvidenceScalar = str | int | float | bool | Decimal


# Only these top-level JSON fields may be selected or used as SQL filters.
APPROVED_STRUCTURED_FIELDS: dict[EvidenceType, frozenset[str]] = {
    EvidenceType.COMPANY_PROFILE: frozenset({"employee_count", "headquarters"}),
    EvidenceType.PROJECT: frozenset({"contract_value", "industry"}),
    EvidenceType.CERTIFICATION: frozenset({"name", "status"}),
    EvidenceType.CAPABILITY: frozenset(),
    EvidenceType.POLICY: frozenset(),
}


@dataclass(frozen=True)
class RuleEvidenceValue:
    """Contains the small exact value returned for one rule."""

    value: RuleEvidenceScalar | None = None
    status: str | None = None
    valid_until: date | None = None
    problem: str | None = None


# ========== Evidence query service ==========

class RuleEvidenceService:
    """Query database for the values needed to evaluate a rule"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def read(self, rule: RuleSpec) -> RuleEvidenceValue:
        """Return only the stored value needed to evaluate one rule"""

        # Check whether the rule can be safely converted into a query.
        problem = _check_rule_query_problem(rule)
        if problem is not None:
            return RuleEvidenceValue(problem=problem)

        #constructs conditions from a selector
        conditions = _build_selector_conditions(rule.evidence_selector)
        
        check = rule.check
        if isinstance(check, MinimumCountCheck):
            statement = select(func.count()).select_from(EvidenceRecord).where(*conditions)

            count = self._session.scalar(statement)
            return RuleEvidenceValue(value=int(count or 0))

        if isinstance(check, (MinimumValueCheck, AllowedValueCheck)):
            return self._read_structured_value(check.value_field, conditions)

        if isinstance(check, ValidUntilCheck):
            return self._read_valid_until(conditions)

        if isinstance(check, CertificationValidityCheck):
            return self._read_certification(conditions)

        raise TypeError(f"unsupported rule check: {type(check).__name__}")

    def _read_structured_value(
        self,
        field: str,
        conditions: list[ColumnElement[bool]],
    ) -> RuleEvidenceValue:
        """Reads one JSON field"""
        
        query_expression = _build_structured_field_expression(field)
        
        statement = select(query_expression).where(*conditions).distinct().limit(2)
        values = list(self._session.scalars(statement).all())
        
        return _single_scalar_value(values)

    def _read_valid_until(
        self,
        conditions: list[ColumnElement[bool]],
    ) -> RuleEvidenceValue:
        
        statement = (
            select(EvidenceRecord.valid_until)
            .where(*conditions)
            .distinct()
            .limit(2)
        )
        
        values = list(self._session.scalars(statement).all())
        if len(values) > 1:
            return RuleEvidenceValue(problem="multiple validity dates matched the rule")
        return RuleEvidenceValue(valid_until=values[0] if values else None)

    def _read_certification(
        self,
        conditions: list[ColumnElement[bool]],
    ) -> RuleEvidenceValue:
        
        status = _build_structured_field_expression("status").as_string()
        
        statement = (
            select(status, EvidenceRecord.valid_until)
            .where(*conditions)
            .distinct()
            .limit(2)
        )
        
        rows = list(self._session.execute(statement).all())
        if len(rows) > 1:
            return RuleEvidenceValue(problem="multiple certification states matched the rule")
        if not rows:
            return RuleEvidenceValue()

        status_value, valid_until = rows[0]
        if status_value is not None and not isinstance(status_value, str):
            return RuleEvidenceValue(problem="certification status is not text")
        return RuleEvidenceValue(status=status_value, valid_until=valid_until)


# ========== Rule query validation ==========

def _check_rule_query_problem(rule: RuleSpec) -> str | None:
    """Check if and why a rule cannot be converted into a safe exact query"""

    selector = rule.evidence_selector
    fields = [item.field for item in selector.filters]
    
    if isinstance(rule.check, (MinimumValueCheck, AllowedValueCheck)):
        fields.append(rule.check.value_field)
        
    if isinstance(rule.check, CertificationValidityCheck):
        if selector.evidence_type is not EvidenceType.CERTIFICATION:
            return "certification validity requires certification evidence"
        if "name" not in fields:
            return "certification validity requires a certification name filter"
        fields.append("status")

    approved_fields = APPROVED_STRUCTURED_FIELDS[selector.evidence_type]
    unsupported_field = next(
        (field for field in fields if field not in approved_fields),
        None,
    )
    if unsupported_field is None:
        return None
    
    return f"evidence field is not approved: {unsupported_field}"


# ========== Evidence selector conditions ==========

def _build_selector_conditions(selector: EvidenceSelector) -> list[ColumnElement[bool]]:
    """Build conditions from a validated evidence selector"""

    conditions: list[ColumnElement[bool]] = [
        EvidenceRecord.evidence_type == selector.evidence_type.value
    ]
    conditions.extend(_filter_condition(item) for item in selector.filters)
    return conditions


def _filter_condition(item: EvidenceFilter) -> ColumnElement[bool]:
    """Match one approved top-level field with a parameterized JSON value."""

    condition = EvidenceRecord.structured_value.contains({item.field: item.equals})
    return cast(ColumnElement[bool], condition)


def _build_structured_field_expression(field: str) -> Any:
    """Create a SQL expression for one field in the structured_value JSON column."""

    return EvidenceRecord.structured_value[field]


# ========== Query result normalization ==========

def _single_scalar_value(values: list[object]) -> RuleEvidenceValue:
    """Return one distinct scalar or describe why it is unclear."""

    if len(values) > 1:
        return RuleEvidenceValue(problem="multiple distinct evidence values matched the rule")
    if not values or values[0] is None:
        return RuleEvidenceValue()

    value = values[0]
    if isinstance(value, (str, int, float, Decimal)):
        return RuleEvidenceValue(value=value)
    return RuleEvidenceValue(problem="the selected evidence value is not a scalar")
