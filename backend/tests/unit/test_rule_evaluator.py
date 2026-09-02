"""Test generic rule evaluation with synthetic evidence values."""

from datetime import date

from app.schemas import Requirement, RequirementType, RuleOutcome, RuleSpec, SourceReference
from app.services.rule_evaluator import DeterministicRuleEvaluator
from app.services.rule_evidence import RuleEvidenceService, RuleEvidenceValue


class FakeRuleEvidenceService(RuleEvidenceService):
    """Returns prepared values without querying a database."""

    def __init__(self, *values: RuleEvidenceValue) -> None:
        self._values = iter(values)

    def read(self, rule: RuleSpec) -> RuleEvidenceValue:
        return next(self._values)


def _rule(
    check: dict[str, object],
    *,
    rule_id: str = "REQ-TEST-001-RULE-001",
    evidence_type: str = "project",
) -> RuleSpec:
    """Build one fictional rule for evaluator tests."""

    return RuleSpec.model_validate(
        {
            "rule_id": rule_id,
            "subject": "fictional evidence",
            "evidence_selector": {"evidence_type": evidence_type},
            "check": check,
        }
    )


def _requirement(*rules: RuleSpec) -> Requirement:
    """Build one fictional requirement with optional rules."""

    return Requirement(
        requirement_id="REQ-TEST-001",
        tender_id="TENDER-TEST-001",
        requirement_text="The bidder must meet a fictional requirement",
        normalized_requirement="Meet a fictional requirement",
        requirement_type=RequirementType.MANDATORY,
        source_page=1,
        source_excerpt="Fictional source text",
        source_references=[
            SourceReference(
                block_id="P001-B001",
                page_number=1,
                bounding_box=(1.0, 1.0, 2.0, 2.0),
            )
        ],
        rules=list(rules),
    )


def test_evaluate_returns_none_when_requirement_has_no_rules() -> None:
    evaluator = DeterministicRuleEvaluator(
        FakeRuleEvidenceService(),
        as_of=date(2026, 1, 1),
    )

    assert evaluator.evaluate(_requirement()) is None


def test_evaluate_minimum_count_passes() -> None:
    rule = _rule({"operator": "minimum_count", "minimum": 2})
    evaluator = DeterministicRuleEvaluator(
        FakeRuleEvidenceService(RuleEvidenceValue(value=3)),
        as_of=date(2026, 1, 1),
    )

    result = evaluator.evaluate(_requirement(rule))

    assert result is not None
    assert result.outcome is RuleOutcome.PASSED
    assert result.rule_type == "minimum_count"


def test_evaluate_missing_value_requires_human_review() -> None:
    rule = _rule(
        {
            "operator": "minimum_value",
            "value_field": "contract_value",
            "minimum": 100_000,
        }
    )
    evaluator = DeterministicRuleEvaluator(
        FakeRuleEvidenceService(RuleEvidenceValue()),
        as_of=date(2026, 1, 1),
    )

    result = evaluator.evaluate(_requirement(rule))

    assert result is not None
    assert result.outcome is RuleOutcome.REQUIRES_HUMAN_REVIEW


def test_evaluate_unclear_query_result_requires_human_review() -> None:
    rule = _rule({"operator": "minimum_count", "minimum": 2})
    evaluator = DeterministicRuleEvaluator(
        FakeRuleEvidenceService(
            RuleEvidenceValue(problem="evidence field is not approved: example")
        ),
        as_of=date(2026, 1, 1),
    )

    result = evaluator.evaluate(_requirement(rule))

    assert result is not None
    assert result.outcome is RuleOutcome.REQUIRES_HUMAN_REVIEW
    assert "not approved" in result.reason


def test_evaluate_validity_uses_analysis_date() -> None:
    rule = _rule({"operator": "valid_until"})
    evaluator = DeterministicRuleEvaluator(
        FakeRuleEvidenceService(RuleEvidenceValue(valid_until=date(2027, 1, 1))),
        as_of=date(2026, 1, 1),
    )

    result = evaluator.evaluate(_requirement(rule))

    assert result is not None
    assert result.outcome is RuleOutcome.PASSED


def test_evaluate_certification_uses_status_and_expiry() -> None:
    rule = _rule(
        {"operator": "certification_validity"},
        evidence_type="certification",
    )
    evaluator = DeterministicRuleEvaluator(
        FakeRuleEvidenceService(
            RuleEvidenceValue(status="valid", valid_until=date(2027, 1, 1))
        ),
        as_of=date(2026, 1, 1),
    )

    result = evaluator.evaluate(_requirement(rule))

    assert result is not None
    assert result.outcome is RuleOutcome.PASSED


def test_evaluate_multiple_rules_returns_first_failure() -> None:
    count_rule = _rule(
        {"operator": "minimum_count", "minimum": 2},
        rule_id="REQ-TEST-001-RULE-001",
    )
    allowed_rule = _rule(
        {
            "operator": "allowed_value",
            "value_field": "industry",
            "allowed_values": ["education"],
        },
        rule_id="REQ-TEST-001-RULE-002",
    )
    evaluator = DeterministicRuleEvaluator(
        FakeRuleEvidenceService(
            RuleEvidenceValue(value=3),
            RuleEvidenceValue(value="retail"),
        ),
        as_of=date(2026, 1, 1),
    )

    result = evaluator.evaluate(_requirement(count_rule, allowed_rule))

    assert result is not None
    assert result.outcome is RuleOutcome.FAILED
    assert result.rule_type == "allowed_value"
