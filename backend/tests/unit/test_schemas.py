from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import (
    AnalysisResult,
    Decision,
    DecisionStatus,
    Evidence,
    EvidenceType,
    OverallRecommendation,
    Requirement,
    RequirementType,
    SourceReference,
    TenderDocument,
    TraceMetadata,
)

"""
Test data models and their validation rules.
"""


# Synthetic values keep public schema tests independent from real tenders
TEST_SHA256 = "a" * 64
TEST_TENDER_ID = "TENDER-TEST-001"
TEST_REQUIREMENT_ID = "REQ-TEST-001"
TEST_REQUIREMENT_TEXT = "The bidder must provide implementation services"


def _requirement(*, source_page: int = 3) -> Requirement:
    return Requirement(
        requirement_id=TEST_REQUIREMENT_ID,
        tender_id=TEST_TENDER_ID,
        requirement_text=TEST_REQUIREMENT_TEXT,
        normalized_requirement="Provide implementation services",
        requirement_type=RequirementType.MANDATORY,
        source_page=source_page,
        source_excerpt=TEST_REQUIREMENT_TEXT,
        source_references=[
            SourceReference(
                block_id=f"P{source_page:03d}-B001",
                page_number=source_page,
                bounding_box=(72.0, 72.0, 500.0, 100.0),
            )
        ],
    )


# Basic tests

def test_tender_doc_normalizes_sha256() -> None:
    tender = TenderDocument(
        tender_id="TENDER-TEST-001",
        title="Example IT Services Tender",
        source_url="https://example.com/tenders/example",
        file_hash=TEST_SHA256,
        local_filename="example_tender.pdf",
    )

    assert tender.file_hash == TEST_SHA256.upper()


def test_evidence_accepts_structured_value_without_text() -> None:
    evidence = Evidence(
        evidence_id="CERT-001",
        evidence_type=EvidenceType.CERTIFICATION,
        structured_value={"name": "Example certification", "status": "valid"},
        valid_from=date(2025, 1, 1),
        valid_until=date(2027, 1, 1),
    )

    assert evidence.supporting_text is None


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


def test_complete_analysis_result_is_valid() -> None:
    requirement = _requirement()
    decision = Decision(
        requirement_id=TEST_REQUIREMENT_ID,
        status=DecisionStatus.SATISFIED,
        evidence_ids=["PROJECT-TEST-001"],
        reason="The project demonstrates relevant implementation experience",
    )

    result = AnalysisResult(
        analysis_id="ANALYSIS-TEST-001",
        tender_id=TEST_TENDER_ID,
        requirements=[requirement],
        decisions=[decision],
        overall_recommendation=OverallRecommendation.BID,
        trace=TraceMetadata(
            document_sha256=TEST_SHA256,
            model_version="mock-model",
            prompt_version="test-v1",
            latency_ms=25,
            input_tokens=100,
            output_tokens=40,
            estimated_cost_usd=0.001,
        ),
    )

    assert result.analysis_id == "ANALYSIS-TEST-001"
    assert result.decisions[0].requirement_id == result.requirements[0].requirement_id


# Corner-case tests

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
        _requirement(source_page=0)


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


def test_analysis_requires_one_decision_per_requirement() -> None:
    requirement = _requirement()

    with pytest.raises(ValidationError, match="at least 1 item"):
        AnalysisResult(
            analysis_id="ANALYSIS-TEST-001",
            tender_id=TEST_TENDER_ID,
            requirements=[requirement],
            decisions=[],
            overall_recommendation=OverallRecommendation.HUMAN_REVIEW,
            trace=TraceMetadata(
                document_sha256=TEST_SHA256,
                model_version="mock-model",
                prompt_version="test-v1",
                latency_ms=25,
            ),
        )
