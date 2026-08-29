from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import (
    Decision,
    DecisionStatus,
    Evidence,
    EvidenceType,
    Requirement,
    RequirementType,
    TenderDocument,
)

TEST_SHA256 = "a" * 64


def test_tender_doc_normalizes_sha256() -> None:
    tender = TenderDocument(
        tender_id="TENDER-TEST-001",
        title="Example IT Services Tender",
        source_url="https://example.com/tenders/example",
        file_hash=TEST_SHA256,
        local_filename="example_tender.pdf",
    )

    assert tender.file_hash == TEST_SHA256.upper()


def test_tender_doc_rejects_invalid_sha256() -> None:
    with pytest.raises(ValidationError):
        TenderDocument(
            tender_id="TENDER-TEST-001",
            title="Example IT Services Tender",
            source_url="https://example.com/tender",
            file_hash="not-a-sha256",
            local_filename="example_tender.pdf",
        )


def test_requirement_requires_a_positive_source_page() -> None:
    with pytest.raises(ValidationError):
        Requirement(
            requirement_id="REQ-001",
            tender_id="TENDER-001",
            requirement_text="The bidder must provide implementation services.",
            normalized_requirement="Provide implementation services.",
            requirement_type=RequirementType.MANDATORY,
            source_page=0,
            source_excerpt="The bidder must provide implementation services.",
        )


def test_evidence_accepts_structured_value_without_text() -> None:
    evidence = Evidence(
        evidence_id="CERT-001",
        evidence_type=EvidenceType.CERTIFICATION,
        structured_value={"name": "Example certification", "status": "valid"},
        valid_from=date(2025, 1, 1),
        valid_until=date(2027, 1, 1),
    )

    assert evidence.supporting_text is None


def test_evidence_requires_content() -> None:
    with pytest.raises(ValidationError, match="supporting_text or structured_value"):
        Evidence(
            evidence_id="PROJECT-001",
            evidence_type=EvidenceType.PROJECT,
        )


def test_evidence_rejects_reversed_validity_dates() -> None:
    with pytest.raises(ValidationError, match="valid_until"):
        Evidence(
            evidence_id="CERT-001",
            evidence_type=EvidenceType.CERTIFICATION,
            supporting_text="Example certification",
            valid_from=date(2027, 1, 1),
            valid_until=date(2025, 1, 1),
        )


def test_satisfied_decision_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="requires at least one evidence ID"):
        Decision(
            requirement_id="REQ-001",
            status=DecisionStatus.SATISFIED,
            evidence_ids=[],
            reason="The requirement is supported.",
        )


def test_satisfied_decision_accepts_evidence() -> None:
    decision = Decision(
        requirement_id="REQ-001",
        status=DecisionStatus.SATISFIED,
        evidence_ids=["PROJECT-001"],
        reason="PROJECT-001 demonstrates the required implementation experience.",
    )

    assert decision.evidence_ids == ["PROJECT-001"]


def test_non_satisfied_decision_can_have_no_evidence() -> None:
    decision = Decision(
        requirement_id="REQ-002",
        status=DecisionStatus.INSUFFICIENT_EVIDENCE,
        reason="No evidence supports this requirement.",
    )

    assert decision.evidence_ids == []
