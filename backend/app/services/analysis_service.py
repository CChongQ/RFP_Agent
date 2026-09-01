from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from app.database.models import (
    ANALYSIS_STATUS_COMPLETED,
    ANALYSIS_STATUS_FAILED,
    ANALYSIS_STATUS_RUNNING,
    AnalysisRunRecord,
    DecisionRecord,
    RequirementRecord,
    TenderRecord,
)
from app.prompts import ANALYSIS_PROMPT_VERSION
from app.schemas import (
    AnalysisResult,
    DecisionStatus,
    ExtractedPage,
    PdfExtractionResult,
    Requirement,
    TenderDocument,
    ToolCallTrace,
    TraceMetadata,
)
from app.services.decision_service import DecisionServiceResult
from app.services.model_usage import ModelUsageSnapshot, ModelUsageTracker, usage_since
from app.services.pdf_extractor import extract_pdf
from app.services.requirement_extractor import RequirementModelClient, extract_requirements

"""
End-to-end orchestration
"""

class AnalysisServiceError(RuntimeError):
    """when an analysis cannot complete safely"""


class PdfExtractor(Protocol):
    def __call__(
        self,
        path: Path,
        *,
        max_pdf_mb: int,
        max_pdf_pages: int,
    ) -> PdfExtractionResult:
        """Extract validated page text and metadata from a PDF"""
        ...


class RequirementExtractor(Protocol):
    def __call__(
        self,
        pages: Sequence[ExtractedPage],
        *,
        tender_id: str,
        model: str,
        client: RequirementModelClient,
        max_chunk_characters: int,
    ) -> list[Requirement]:
        """Turn extracted PDF pages into validated requirements"""
        ...


class DecisionRunner(Protocol):
    def decide(self, requirements: Sequence[Requirement]) -> DecisionServiceResult:
        """Create decisions and an overall recommendation"""
        ...


@dataclass
class AnalysisProgress:
    started_at: float
    usage_before: ModelUsageSnapshot
    requirement_ids: list[str] = field(default_factory=list)
    requirement_source_block_ids: dict[str, list[str]] = field(default_factory=dict)
    tool_calls: list[ToolCallTrace] = field(default_factory=list)


class AnalysisService:
    """Runs and persists one explicit end-to-end analysis flow"""

    def __init__(
        self,
        session: Session,
        requirement_client: RequirementModelClient,
        decision_service: DecisionRunner,
        *,
        model: str,
        usage_tracker: ModelUsageTracker | None = None,
        max_pdf_mb: int = 25,
        max_pdf_pages: int = 250,
        max_chunk_characters: int = 12_000,
        pdf_extractor: PdfExtractor = extract_pdf,
        requirement_extractor: RequirementExtractor = extract_requirements,
        analysis_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = perf_counter,
    ) -> None:

        if not model.strip():
            raise ValueError("model cannot be empty")
        if max_pdf_mb < 1 or max_pdf_pages < 1 or max_chunk_characters < 1:
            raise ValueError("analysis limits must be positive")
        
        self._session = session
        self._requirement_client = requirement_client
        self._decision_service = decision_service
        self._model = model
        self._usage_tracker = usage_tracker or ModelUsageTracker()
        
        self._max_pdf_mb = max_pdf_mb
        self._max_pdf_pages = max_pdf_pages
        self._max_chunk_characters = max_chunk_characters
        self._pdf_extractor = pdf_extractor
        self._requirement_extractor = requirement_extractor
        self._analysis_id_factory = analysis_id_factory or _new_analysis_id
        self._clock = clock
        self._prompt_version = ANALYSIS_PROMPT_VERSION

    def analyze(self, tender: TenderDocument, pdf_path: Path) -> AnalysisResult:
        """Run the vertical slice and flush its records in the caller transaction"""

        progress = AnalysisProgress(
            started_at=self._clock(),
            usage_before=self._usage_tracker.snapshot(),
        )
        
        analysis_id = self._analysis_id_factory()
        self._persist_tender(tender)
        analysis_record = self._start_analysis_record(analysis_id, tender)

        try:
            extraction = self._extract_pdf(tender, pdf_path)
            
            #get requirements
            requirements = self._extract_requirements(tender, extraction)
            progress.requirement_ids = [item.requirement_id for item in requirements]
            progress.requirement_source_block_ids = {
                item.requirement_id: [
                    reference.block_id for reference in item.source_references
                ]
                for item in requirements
            }
            self._persist_requirements(requirements)
            
            #make decision
            decision_result = self._decision_service.decide(requirements)
            
            #make analysis 
            progress.tool_calls = decision_result.tool_calls
            result = self._build_result(
                analysis_id=analysis_id,
                tender=tender,
                requirements=requirements,
                decision_result=decision_result,
                document_sha256=extraction.document_sha256,
                progress=progress,
            )
            
            #save result
            self._complete_analysis_record(analysis_record, result)
            self._persist_decisions(analysis_id, decision_result)
            
            self._session.flush()
            return result
        except Exception as exc:
            self._mark_analysis_failed(analysis_record, tender, progress, exc)
            raise

    def _persist_tender(self, tender: TenderDocument) -> None:
        # tender must exist before child rows can use its ID
        self._session.merge(
            TenderRecord(
                id=tender.tender_id,
                title=tender.title,
                source_url=str(tender.source_url),
                local_filename=tender.local_filename,
                sha256=tender.file_hash,
            )
        )
        self._session.flush()

    def _start_analysis_record(
        self,
        analysis_id: str,
        tender: TenderDocument,
    ) -> AnalysisRunRecord:
        """Create the running database record for a new analysis"""

        record = AnalysisRunRecord(
            id=analysis_id,
            tender_id=tender.tender_id,
            status=ANALYSIS_STATUS_RUNNING,
            document_sha256=tender.file_hash,
            model_version=self._model,
            prompt_version=self._prompt_version,
            trace={},
        )
        self._session.add(record)
        return record

    def _extract_pdf(
        self,
        tender: TenderDocument,
        pdf_path: Path,
    ) -> PdfExtractionResult:
        """Extract PDF and confirm that its hash matches the record"""

        extraction = self._pdf_extractor(
            Path(pdf_path),
            max_pdf_mb=self._max_pdf_mb,
            max_pdf_pages=self._max_pdf_pages,
        )
        
        if extraction.document_sha256 != tender.file_hash:
            raise AnalysisServiceError(
                "PDF hash does not match the selected tender manifest record"
            )
        return extraction

    def _extract_requirements(
        self,
        tender: TenderDocument,
        extraction: PdfExtractionResult,
    ) -> list[Requirement]:
        """Extract structured requirements from the PDF pages"""

        return self._requirement_extractor(
            extraction.pages,
            tender_id=tender.tender_id,
            model=self._model,
            client=self._requirement_client,
            max_chunk_characters=self._max_chunk_characters,
        )

    def _build_result(
        self,
        *,
        analysis_id: str,
        tender: TenderDocument,
        requirements: list[Requirement],
        decision_result: DecisionServiceResult,
        document_sha256: str,
        progress: AnalysisProgress,
    ) -> AnalysisResult:
        """Combine requirements, decisions, risks, and trace data"""

        return AnalysisResult(
            analysis_id=analysis_id,
            tender_id=tender.tender_id,
            requirements=requirements,
            decisions=decision_result.decisions,
            overall_recommendation=decision_result.overall_recommendation,
            risks=_risk_reasons(decision_result),
            human_review_reasons=_human_review_reasons(decision_result),
            trace=self._build_trace(document_sha256, progress),
        )

    def _build_trace(
        self,
        document_sha256: str,
        progress: AnalysisProgress,
        *,
        errors: list[str] | None = None,
    ) -> TraceMetadata:
        """Build audit data"""

        usage = usage_since(progress.usage_before, self._usage_tracker.snapshot())
        
        return TraceMetadata(
            document_sha256=document_sha256,
            model_version=self._model,
            prompt_version=self._prompt_version,
            latency_ms=self._elapsed_ms(progress.started_at),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
            extracted_requirement_ids=progress.requirement_ids,
            requirement_source_block_ids=progress.requirement_source_block_ids,
            tool_calls=progress.tool_calls,
            errors=errors or [],
        )

    def _mark_analysis_failed(
        self,
        record: AnalysisRunRecord,
        tender: TenderDocument,
        progress: AnalysisProgress,
        error: Exception,
    ) -> None:
        """failed the analysis failed and save a short error trace"""

        error_summary = f"{type(error).__name__}: {error}"
        record.status = ANALYSIS_STATUS_FAILED
        record.trace = self._build_trace(
            tender.file_hash,
            progress,
            errors=[error_summary],
        ).model_dump(mode="json")
        
        self._session.flush()

    def _persist_requirements(self, requirements: Sequence[Requirement]) -> None:
        """Insert or update all requirements produced by extraction"""

        for requirement in requirements:
            self._session.merge(
                RequirementRecord(
                    id=requirement.requirement_id,
                    tender_id=requirement.tender_id,
                    requirement_text=requirement.requirement_text,
                    normalized_requirement=requirement.normalized_requirement,
                    requirement_type=requirement.requirement_type.value,
                    source_page=requirement.source_page,
                    source_excerpt=requirement.source_excerpt,
                    source_references=[
                        reference.model_dump(mode="json")
                        for reference in requirement.source_references
                    ],
                    requires_human_review=requirement.requires_human_review,
                )
            )
        self._session.flush()

    def _complete_analysis_record(
        self,
        record: AnalysisRunRecord,
        result: AnalysisResult,
    ) -> None:
        """Mark the analysis complete and save its final trace"""

        record.status = ANALYSIS_STATUS_COMPLETED
        record.document_sha256 = result.trace.document_sha256
        record.overall_recommendation = result.overall_recommendation.value
        record.trace = result.trace.model_dump(mode="json")

    def _persist_decisions(
        self,
        analysis_id: str,
        result: DecisionServiceResult,
    ) -> None:
        """Insert or update every decision for the analysis"""

        for decision in result.decisions:
            self._session.merge(
                DecisionRecord(
                    analysis_id=analysis_id,
                    requirement_id=decision.requirement_id,
                    status=decision.status.value,
                    evidence_ids=decision.evidence_ids,
                    reason=decision.reason,
                    rule_result=(
                        decision.rule_result.model_dump(mode="json")
                        if decision.rule_result is not None
                        else None
                    ),
                )
            )

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, round((self._clock() - started_at) * 1000))


def _new_analysis_id() -> str:
    return f"ANALYSIS-{uuid4()}"


def _risk_reasons(result: DecisionServiceResult) -> list[str]:
    """Collect reasons from decisions that shown as risks"""

    return [
        decision.reason
        for decision in result.decisions
        if decision.status
        in {DecisionStatus.PARTIALLY_SATISFIED, DecisionStatus.NOT_SATISFIED}
    ]


def _human_review_reasons(result: DecisionServiceResult) -> list[str]:
    """Collect reasons from decisions that need human review"""

    return [
        decision.reason
        for decision in result.decisions
        if decision.status
        in {
            DecisionStatus.INSUFFICIENT_EVIDENCE,
            DecisionStatus.REQUIRES_HUMAN_REVIEW,
        }
    ]
