import json
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
from app.services.analysis_progress import ProgressReporter
from app.services.analysis_result_export import export_analysis_result
from app.services.analysis_service import AnalysisService, AnalysisServiceError
from app.services.decision_service import DecisionServiceResult
from app.services.model_usage import ModelUsageTracker

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
    def decide(
        self,
        requirements: Sequence[Requirement],
        *,
        progress_reporter: ProgressReporter | None = None,
    ) -> DecisionServiceResult:
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

@pytest.mark.parametrize(
    "input_price,output_price,missing_usage,expected_cost",
    [
        (None, None, False, None),
        (2.0, 8.0, False, 0.00052),
        (2.0, 8.0, True, None),
    ],
)
def test_analysis_service_builds_trace_and_flushes_records(
    tmp_path: Path,
    input_price: float | None,
    output_price: float | None,
    missing_usage: bool,
    expected_cost: float | None,
) -> None:
    # verifies orchestration and persistence logic, with all syntetic data 
    
    session = Mock(spec=Session)
    requirement = _requirement()
    pdf_path = Path("synthetic.pdf")
    usage_tracker = ModelUsageTracker(
        input_cost_per_mil_tokens=input_price,
        output_cost_per_mil_tokens=output_price,
    )

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
        progress_reporter: ProgressReporter | None = None,
    ) -> list[Requirement]:
        usage_tracker.add(input_tokens=100, output_tokens=40)
        if missing_usage:
            usage_tracker.record_missing_usage()
        return [requirement]

    service = AnalysisService(
        session,
        Mock(),
        FakeDecisionRunner(),
        model="mock-model",
        usage_tracker=usage_tracker,
        pdf_extractor=fake_pdf_extractor,
        requirement_extractor=fake_requirement_extractor,
        analysis_id_factory=lambda: "ANALYSIS-TEST-001",
        progress_reporter_factory=lambda **_: Mock(spec=ProgressReporter),
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
    assert result.trace.latency_scope == "analysis_stage"
    assert result.trace.usage_scope == "extraction_n_assess_only"
    assert result.trace.usage_complete is (not missing_usage)
    assert result.trace.input_tokens == 100
    assert result.trace.output_tokens == 40
    if expected_cost is None:
        assert result.trace.estimated_cost_usd is None
    else:
        assert result.trace.estimated_cost_usd == pytest.approx(expected_cost)
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
    assert analysis_record.trace == result.trace.model_dump(mode="json")

    exported_path = export_analysis_result(result, tmp_path)
    exported = json.loads(exported_path.read_text(encoding="utf-8"))
    assert exported["schema_version"] == "1.1"
    assert exported["run_metrics"] == {
        "latency_ms": 25,
        "latency_scope": "analysis_stage",
        "input_tokens": 100,
        "output_tokens": 40,
        "usage_scope": "extraction_n_assess_only",
        "usage_complete": not missing_usage,
        "estimated_cost_usd": result.trace.estimated_cost_usd,
    }


# Corner-case tests

def test_analysis_result_explains_missing_mandatory_requirements() -> None:
    
    requirement = _requirement().model_copy(
        update={"requirement_type": RequirementType.INFORMATIONAL}
    )
    
    decision_result = FakeDecisionRunner().decide([requirement])
    
    runner = Mock()
    runner.decide.return_value = DecisionServiceResult(
        decisions=decision_result.decisions,
        overall_recommendation=OverallRecommendation.HUMAN_REVIEW,
        tool_calls=decision_result.tool_calls,
    )
    
    pdf_path = Path("synthetic.pdf")
    service = AnalysisService(
        Mock(spec=Session),
        Mock(),
        runner,
        model="mock-model",
        pdf_extractor=Mock(return_value=_pdf_result(pdf_path)),
        requirement_extractor=Mock(return_value=[requirement]),
        progress_reporter_factory=lambda **_: Mock(spec=ProgressReporter),
    )

    result = service.analyze(_tender(), pdf_path)

    assert result.overall_recommendation is OverallRecommendation.HUMAN_REVIEW
    assert any(
        "no mandatory requirements" in reason.casefold()
        for reason in result.human_review_reasons
    )


def test_analysis_service_records_hash_mismatch_failure() -> None:
    session = Mock(spec=Session)
    reporter = Mock(spec=ProgressReporter)

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
        progress_reporter_factory=lambda **_: reporter,
        clock=iter([20.0, 20.010]).__next__,
    )

    with pytest.raises(AnalysisServiceError, match="hash"):
        service.analyze(_tender(), Path("synthetic.pdf"))

    analysis_record = session.add.call_args.args[0]
    assert analysis_record.status == "failed"
    assert analysis_record.trace["errors"][0].startswith("AnalysisServiceError")
    reporter.analysis_failed.assert_called_once()
