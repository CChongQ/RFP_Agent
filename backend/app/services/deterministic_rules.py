from collections.abc import Collection
from datetime import date
from decimal import Decimal, InvalidOperation

from app.schemas import DeterministicRuleResult, RuleOutcome

"""Apply deterministic rule checks to evidence"""


type Numeric = Decimal | int | float

RULE_VALID_UNTIL = "valid_until"
RULE_ALLOWED_VALUE = "allowed_value"
RULE_CERTIFICATION_VALIDITY = "certification_validity"
VALID_CERTIFICATION_STATUSES = frozenset({"active", "valid"})

def _required_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _to_decimal(value: Numeric, *, name: str) -> Decimal:
    # Reject booleans and non-finite values

    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise ValueError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def validate_minimum(
    actual: Numeric | None,
    required: Numeric,
    *,
    subject: str = "value",
    rule_type: str = "minimum_value",
) -> DeterministicRuleResult:
    """Compare the company actual value with the required inclusive minimum"""

    subject = _required_text(subject, name="subject")
    rule_type = _required_text(rule_type, name="rule_type")
    required_value = _to_decimal(required, name="required")

    # Missing evidence trigger human review
    if actual is None:
        return DeterministicRuleResult(
            rule_type=rule_type,
            outcome=RuleOutcome.REQUIRES_HUMAN_REVIEW,
            reason=f"{subject} is missing; minimum {_format_decimal(required_value)} required",
        )

    try:
        actual_value = _to_decimal(actual, name="actual")
    except ValueError:
        return DeterministicRuleResult(
            rule_type=rule_type,
            outcome=RuleOutcome.REQUIRES_HUMAN_REVIEW,
            reason=f"{subject} is not a valid number",
        )

    if actual_value >= required_value:
        return DeterministicRuleResult(
            rule_type=rule_type,
            outcome=RuleOutcome.PASSED,
            reason=(
                f"{subject} {_format_decimal(actual_value)} meets minimum "
                f"{_format_decimal(required_value)}"
            ),
        )
    return DeterministicRuleResult(
        rule_type=rule_type,
        outcome=RuleOutcome.FAILED,
        reason=(
            f"{subject} {_format_decimal(actual_value)} is below minimum "
            f"{_format_decimal(required_value)}"
        ),
    )


def validate_valid_until(
    valid_until: date | None,
    *,
    as_of: date,
    subject: str = "evidence",
) -> DeterministicRuleResult:
    """Check evidence remains valid on an inclusive reference date"""

    subject = _required_text(subject, name="subject")

    if valid_until is None:
        return DeterministicRuleResult(
            rule_type=RULE_VALID_UNTIL,
            outcome=RuleOutcome.REQUIRES_HUMAN_REVIEW,
            reason=f"{subject} expiry date is missing",
        )

    if valid_until >= as_of:
        return DeterministicRuleResult(
            rule_type=RULE_VALID_UNTIL,
            outcome=RuleOutcome.PASSED,
            reason=f"{subject} is valid through {valid_until.isoformat()}",
        )

    return DeterministicRuleResult(
        rule_type=RULE_VALID_UNTIL,
        outcome=RuleOutcome.FAILED,
        reason=f"{subject} expired on {valid_until.isoformat()}",
    )


def validate_allowed_value(
    actual: str | None,
    allowed_values: Collection[str],
    *,
    subject: str = "value",
) -> DeterministicRuleResult:
    """Check value against an allow-list"""

    subject = _required_text(subject, name="subject")

    if isinstance(allowed_values, str):
        raise ValueError("allowed_values must be a collection of values")

    #norm allowed
    normalized_allowed: set[str] = set()
    for value in allowed_values:
        normalized_value = _required_text(value, name="allowed value").casefold()
        normalized_allowed.add(normalized_value)
    if not normalized_allowed:
        raise ValueError("allowed_values cannot be empty")
    
    if actual is None or not actual.strip():
        return DeterministicRuleResult(
            rule_type=RULE_ALLOWED_VALUE,
            outcome=RuleOutcome.REQUIRES_HUMAN_REVIEW,
            reason=f"{subject} is missing",
        )

    #norm actual value 
    actual_value = actual.strip()
    normalized_actual = actual_value.casefold()
    if normalized_actual in normalized_allowed:
        return DeterministicRuleResult(
            rule_type=RULE_ALLOWED_VALUE,
            outcome=RuleOutcome.PASSED,
            reason=f"{subject} {actual_value} is allowed",
        )

    return DeterministicRuleResult(
        rule_type=RULE_ALLOWED_VALUE,
        outcome=RuleOutcome.FAILED,
        reason=f"{subject} {actual_value} is not allowed",
    )


def validate_certification(
    status: str | None,
    valid_until: date | None,
    *,
    as_of: date,
    subject: str = "certification",
) -> DeterministicRuleResult:
    """Check a certification has valid status and has not expired"""

    subject = _required_text(subject, name="subject")

    if status is None or not status.strip() or valid_until is None:
        return DeterministicRuleResult(
            rule_type=RULE_CERTIFICATION_VALIDITY,
            outcome=RuleOutcome.REQUIRES_HUMAN_REVIEW,
            reason=f"{subject} status or expiry date is missing",
        )

    status_value = status.strip()
    normalized_status = status_value.casefold()
    if normalized_status not in VALID_CERTIFICATION_STATUSES:
        return DeterministicRuleResult(
            rule_type=RULE_CERTIFICATION_VALIDITY,
            outcome=RuleOutcome.FAILED,
            reason=f"{subject} status is {status_value}",
        )

    if valid_until < as_of:
        return DeterministicRuleResult(
            rule_type=RULE_CERTIFICATION_VALIDITY,
            outcome=RuleOutcome.FAILED,
            reason=f"{subject} expired on {valid_until.isoformat()}",
        )

    return DeterministicRuleResult(
        rule_type=RULE_CERTIFICATION_VALIDITY,
        outcome=RuleOutcome.PASSED,
        reason=f"{subject} is valid through {valid_until.isoformat()}",
    )
