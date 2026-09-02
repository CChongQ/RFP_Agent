from collections.abc import Sequence
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.database.models import AnalysisRunRecord, DecisionRecord, RequirementRecord
from app.schemas import (
    Decision,
    DecisionStatus,
    ExtractedBlock,
    ExtractedPage,
    OverallRecommendation,
    PdfExtractionResult,
    Requirement,
    RequirementType,
    SourceReference,
    TenderDocument,
    ToolCallTrace,
)
from app.services.analysis_service import AnalysisService, AnalysisServiceError
from app.services.decision_service import DecisionServiceResult

"""
Test the service that runs and saves the full analysis flow.

"""

# use synthetic stages so below tests use no PDF database or API
TEST_SHA256 = "A" * 64


def _tender() -> TenderDocument:
    return TenderDocument(
        tender_id="TENDER-TEST-001",
        title="Synthetic ATS Tender",
        source_url="https://example.com/tenders/synthetic",
        file_hash=TEST_SHA256,
        local_filename="synthetic.pdf",
    )


def _pdf_result(path: Path, document_sha256: str = TEST_SHA256) -> PdfExtractionResult:
    return PdfExtractionResult(
        source_path=path,
        document_sha256=document_sha256,
        file_size_bytes=100,
        page_count=1,
        total_characters=60,
        pages=[
            ExtractedPage(
                page_number=1,
                text="The bidder must demonstrate implementation experience",
                blocks=[
                    ExtractedBlock(
                        block_id="P001-B001",
                        page_number=1,
                        text="The bidder must demonstrate implementation experience",
                        bounding_box=(72.0, 72.0, 500.0, 100.0),
                    )
                ],
            )
        ],
    )


def _requirement() -> Requirement:
    return Requirement(
        requirement_id="TENDER-TEST-001-REQ-001",
        tender_id="TENDER-TEST-001",
        requirement_text="The bidder must demonstrate implementation experience",
        normalized_requirement="Demonstrate implementation experience",
        requirement_type=RequirementType.MANDATORY,
        source_page=1,
        source_excerpt="The bidder must demonstrate implementation experience",
        source_references=[
            SourceReference(
                block_id="P001-B001",
                page_number=1,
                bounding_box=(72.0, 72.0, 500.0, 100.0),
            )
        ],
    )


class FakeDecisionRunner:
    def decide(self, requirements: Sequence[Requirement]) -> DecisionServiceResult:
        requirement = requirements[0]
        return DecisionServiceResult(
            decisions=[
                Decision(
                    requirement_id=requirement.requirement_id,
                    status=DecisionStatus.SATISFIED,
                    evidence_ids=["PROJECT-TEST-001"],
                    reason="Synthetic project evidence supports the requirement",
                )
            ],
            overall_recommendation=OverallRecommendation.BID,
            tool_calls=[
                ToolCallTrace(
                    requirement_id=requirement.requirement_id,
                    tool_name="search_company_evidence",
                    arguments={"query": requirement.normalized_requirement, "top_k": 5},
                    result_ids=["PROJECT-TEST-001"],
                    scores=[0.9],
                )
            ],
        )


# Basic tests

def test_analysis_service_builds_trace_and_flushes_records() -> None:
    # verifies orchestration and persistence logic, with all syntetic data 
    
    session = Mock(spec=Session)
    requirement = _requirement()
    pdf_path = Path("synthetic.pdf")

    def fake_pdf_extractor(
        path: Path,
        *,
        max_pdf_mb: int,
        max_pdf_pages: int,
    ) -> PdfExtractionResult:
        return _pdf_result(path)

    def fake_requirement_extractor(
        pages: Sequence[ExtractedPage],
        *,
        tender_id: str,
        model: str,
        client: object,
        max_chunk_characters: int,
    ) -> list[Requirement]:
        return [requirement]

    service = AnalysisService(
        session,
        Mock(),
        FakeDecisionRunner(),
        model="mock-model",
        pdf_extractor=fake_pdf_extractor,
        requirement_extractor=fake_requirement_extractor,
        analysis_id_factory=lambda: "ANALYSIS-TEST-001",
        clock=iter([10.0, 10.025]).__next__,
    )

    result = service.analyze(_tender(), pdf_path)

    assert result.analysis_id == "ANALYSIS-TEST-001"
    assert result.overall_recommendation is OverallRecommendation.BID
    assert result.trace.extracted_requirement_ids == [requirement.requirement_id]
    assert result.trace.requirement_source_block_ids == {
        requirement.requirement_id: ["P001-B001"]
    }
    assert result.trace.tool_calls[0].result_ids == ["PROJECT-TEST-001"]
    assert result.trace.latency_ms == 25
    assert any(
        isinstance(call.args[0], RequirementRecord)
        for call in session.merge.call_args_list
    )
    requirement_record = next(
        call.args[0]
        for call in session.merge.call_args_list
        if isinstance(call.args[0], RequirementRecord)
    )
    assert requirement_record.source_references[0]["block_id"] == "P001-B001"
    assert any(
        isinstance(call.args[0], DecisionRecord) for call in session.merge.call_args_list
    )
    analysis_record = session.add.call_args.args[0]
    assert isinstance(analysis_record, AnalysisRunRecord)
    assert analysis_record.status == "completed"


# Corner-case tests

def test_analysis_service_records_hash_mismatch_failure() -> None:
    session = Mock(spec=Session)

    def wrong_hash_pdf_extractor(
        path: Path,
        *,
        max_pdf_mb: int,
        max_pdf_pages: int,
    ) -> PdfExtractionResult:
        return _pdf_result(path, document_sha256="B" * 64)

    service = AnalysisService(
        session,
        Mock(),
        FakeDecisionRunner(),
        model="mock-model",
        pdf_extractor=wrong_hash_pdf_extractor,
        analysis_id_factory=lambda: "ANALYSIS-TEST-002",
        clock=iter([20.0, 20.010]).__next__,
    )

    with pytest.raises(AnalysisServiceError, match="hash"):
        service.analyze(_tender(), Path("synthetic.pdf"))

    analysis_record = session.add.call_args.args[0]
    assert analysis_record.status == "failed"
    assert analysis_record.trace["errors"][0].startswith("AnalysisServiceError")
