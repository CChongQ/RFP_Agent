"""Validate generic rule configuration with synthetic values."""

import pytest
from pydantic import ValidationError

from app.schemas import RuleCandidate, RuleOperator, RuleSpec
from app.schemas.rules import MinimumCountCheck


def _minimum_count_rule(*, rule_id: str = "RULE-MINIMUM-PROJECTS") -> dict[str, object]:
    """Build one fictional minimum-count rule payload."""

    return {
        "rule_id": rule_id,
        "subject": "qualifying projects",
        "evidence_selector": {
            "evidence_type": "project",
            "filters": [
                {"field": "industry", "equals": "public_sector"},
            ],
        },
        "check": {"operator": "minimum_count", "minimum": 3},
    }


def test_rule_spec_parses_typed_check() -> None:
    rule = RuleSpec.model_validate(_minimum_count_rule())

    assert isinstance(rule.check, MinimumCountCheck)
    assert rule.check.operator is RuleOperator.MINIMUM_COUNT
    assert rule.check.minimum == 3


def test_rule_spec_rejects_missing_check_value() -> None:
    rule = _minimum_count_rule()
    rule["check"] = {"operator": "minimum_count"}

    with pytest.raises(ValidationError, match="minimum"):
        RuleSpec.model_validate(rule)


def test_rule_spec_rejects_unsupported_operator() -> None:
    rule = _minimum_count_rule()
    rule["check"] = {"operator": "unsupported"}

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        RuleSpec.model_validate(rule)


def test_rule_candidate_does_not_accept_model_generated_id() -> None:
    candidate = _minimum_count_rule()

    with pytest.raises(ValidationError, match="rule_id"):
        RuleCandidate.model_validate(candidate)
