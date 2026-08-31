from datetime import date

from app.schemas import RuleOutcome
from app.services.deterministic_rules import (
    validate_allowed_value,
    validate_certification,
    validate_minimum,
    validate_valid_until,
)

"""
Test fixed rules used for values, dates, and certifications
"""

# Basic tests

def test_validate_minimum_accepts_exact_boundary() -> None:
    assert validate_minimum(3, 3, subject="reference projects").outcome is RuleOutcome.PASSED


def test_validate_valid_until_accepts_reference_date() -> None:
    as_of = date(2026, 8, 29)

    assert validate_valid_until(as_of, as_of=as_of).outcome is RuleOutcome.PASSED


def test_validate_allowed_value_is_case_insensitive() -> None:
    allowed = {"Canada", "United States"}

    result = validate_allowed_value("canada", allowed, subject="hosting region")

    assert result.outcome is RuleOutcome.PASSED


def test_validate_certification_accepts_valid_current_record() -> None:
    as_of = date(2026, 8, 29)

    assert (
        validate_certification("valid", as_of, as_of=as_of).outcome is RuleOutcome.PASSED
    )


# Corner-case tests

def test_validate_minimum_rejects_low_and_missing_values() -> None:
    assert validate_minimum(2, 3, subject="reference projects").outcome is RuleOutcome.FAILED
    assert (
        validate_minimum(None, 3, subject="reference projects").outcome
        is RuleOutcome.REQUIRES_HUMAN_REVIEW
    )


def test_validate_valid_until_rejects_expired_and_missing_dates() -> None:
    as_of = date(2026, 8, 29)

    assert validate_valid_until(date(2026, 8, 28), as_of=as_of).outcome is RuleOutcome.FAILED
    assert (
        validate_valid_until(None, as_of=as_of).outcome
        is RuleOutcome.REQUIRES_HUMAN_REVIEW
    )


def test_validate_allowed_value_rejects_unknown_and_missing_values() -> None:
    allowed = {"Canada", "United States"}

    assert validate_allowed_value("France", allowed).outcome is RuleOutcome.FAILED
    assert validate_allowed_value(None, allowed).outcome is RuleOutcome.REQUIRES_HUMAN_REVIEW


def test_validate_certification_rejects_invalid_or_missing_record() -> None:
    as_of = date(2026, 8, 29)

    assert (
        validate_certification("expired", as_of, as_of=as_of).outcome is RuleOutcome.FAILED
    )
    assert (
        validate_certification("valid", date(2026, 8, 28), as_of=as_of).outcome
        is RuleOutcome.FAILED
    )
    assert (
        validate_certification(None, None, as_of=as_of).outcome
        is RuleOutcome.REQUIRES_HUMAN_REVIEW
    )
