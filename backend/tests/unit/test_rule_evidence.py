"""Test exact rule-evidence reads with synthetic values."""

from datetime import date
from unittest.mock import Mock

from sqlalchemy.orm import Session

from app.schemas import RuleSpec
from app.services.rule_evidence import RuleEvidenceService


def _rule(
    *,
    evidence_type: str = "project",
    filters: list[dict[str, object]] | None = None,
    check: dict[str, object] | None = None,
) -> RuleSpec:
    """Build one fictional validated rule."""

    return RuleSpec.model_validate(
        {
            "rule_id": "REQ-TEST-001-RULE-001",
            "subject": "fictional company evidence",
            "evidence_selector": {
                "evidence_type": evidence_type,
                "filters": filters or [],
            },
            "check": check or {"operator": "minimum_count", "minimum": 2},
        }
    )


def test_read_counts_all_exact_matches() -> None:
    session = Mock(spec=Session)
    session.scalar.return_value = 3
    service = RuleEvidenceService(session)
    rule = _rule(filters=[{"field": "industry", "equals": "education"}])

    result = service.read(rule)

    assert result.value == 3
    assert result.problem is None
    session.scalar.assert_called_once()


def test_read_rejects_unapproved_field_before_querying() -> None:
    session = Mock(spec=Session)
    service = RuleEvidenceService(session)
    rule = _rule(filters=[{"field": "unapproved_field", "equals": "example"}])

    result = service.read(rule)

    assert result.problem == "evidence field is not approved: unapproved_field"
    session.scalar.assert_not_called()
    session.scalars.assert_not_called()
    session.execute.assert_not_called()


def test_read_returns_one_structured_value() -> None:
    values = Mock()
    values.all.return_value = [250_000]
    session = Mock(spec=Session)
    session.scalars.return_value = values
    service = RuleEvidenceService(session)
    rule = _rule(
        check={
            "operator": "minimum_value",
            "value_field": "contract_value",
            "minimum": 100_000,
        }
    )

    result = service.read(rule)

    assert result.value == 250_000
    assert result.problem is None


def test_read_marks_multiple_values_as_unclear() -> None:
    values = Mock()
    values.all.return_value = [100_000, 250_000]
    session = Mock(spec=Session)
    session.scalars.return_value = values
    service = RuleEvidenceService(session)
    rule = _rule(
        check={
            "operator": "minimum_value",
            "value_field": "contract_value",
            "minimum": 100_000,
        }
    )

    result = service.read(rule)

    assert result.problem == "multiple distinct evidence values matched the rule"


def test_read_returns_missing_value_without_guessing() -> None:
    values = Mock()
    values.all.return_value = []
    session = Mock(spec=Session)
    session.scalars.return_value = values
    service = RuleEvidenceService(session)
    rule = _rule(
        evidence_type="company_profile",
        check={
            "operator": "allowed_value",
            "value_field": "headquarters",
            "allowed_values": ["Example City"],
        },
    )

    result = service.read(rule)

    assert result.value is None
    assert result.problem is None


def test_read_returns_one_validity_date() -> None:
    values = Mock()
    values.all.return_value = [date(2030, 1, 1)]
    session = Mock(spec=Session)
    session.scalars.return_value = values
    service = RuleEvidenceService(session)
    rule = _rule(check={"operator": "valid_until"})

    result = service.read(rule)

    assert result.valid_until == date(2030, 1, 1)
    assert result.problem is None


def test_read_returns_one_certification_state() -> None:
    rows = Mock()
    rows.all.return_value = [("valid", date(2030, 1, 1))]
    session = Mock(spec=Session)
    session.execute.return_value = rows
    service = RuleEvidenceService(session)
    rule = _rule(
        evidence_type="certification",
        filters=[{"field": "name", "equals": "Example Certification"}],
        check={"operator": "certification_validity"},
    )

    result = service.read(rule)

    assert result.status == "valid"
    assert result.valid_until == date(2030, 1, 1)
    assert result.problem is None


def test_read_requires_certification_name_filter() -> None:
    session = Mock(spec=Session)
    service = RuleEvidenceService(session)
    rule = _rule(
        evidence_type="certification",
        check={"operator": "certification_validity"},
    )

    result = service.read(rule)

    assert result.problem == (
        "certification validity requires a certification name filter"
    )
    session.execute.assert_not_called()
