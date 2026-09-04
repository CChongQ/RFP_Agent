from pathlib import Path
from typing import Protocol

from app.schemas import AnalysisPrecheck, PdfInspectionResult, TenderDocument
from app.services.analysis_progress import (
    AnalysisEventReporter,
    ProgressReporterFactory,
)
from app.services.pdf_extractor import PdfExtractionError, inspect_pdf


class PdfInspector(Protocol):
    def __call__(
        self,
        path: Path,
        *,
        max_pdf_mb: int,
        max_pdf_pages: int,
    ) -> PdfInspectionResult: ...


class AnalysisPrecheckRunner(Protocol):
    def inspect(
        self,
        tender: TenderDocument,
        pdf_path: Path,
    ) -> AnalysisPrecheck: ...


class AnalysisPrecheckService:
    """Precheck PDF before everything start"""

    def __init__(
        self,
        *,
        max_pdf_mb: int,
        max_pdf_pages: int,
        page_threshold: int,
        pdf_inspector: PdfInspector = inspect_pdf,
        progress_reporter_factory: ProgressReporterFactory = AnalysisEventReporter,
    ) -> None:
        self._max_pdf_mb = max_pdf_mb
        self._max_pdf_pages = max_pdf_pages
        self._page_threshold = page_threshold # check for long file 
        
        self._pdf_inspector = pdf_inspector
        
        self._progress_reporter_factory = progress_reporter_factory

    def inspect(
        self,
        tender: TenderDocument,
        pdf_path: Path,
    ) -> AnalysisPrecheck:
        
        reporter = self._progress_reporter_factory(tender_id=tender.tender_id)
        reporter.precheck_started()
        
        try:
            inspection = self._inspect_and_validate(tender, pdf_path)
            warnings = self._warnings(inspection.page_count)
            
            result = AnalysisPrecheck(
                tender_id=tender.tender_id,
                filename=tender.local_filename,
                document_sha256=inspection.document_sha256,
                file_size_bytes=inspection.file_size_bytes,
                file_size_mb=round(inspection.file_size_bytes / (1024 * 1024), 2),
                page_count=inspection.page_count,
                requires_confirmation=bool(warnings),
                warnings=warnings,
            )
            reporter.precheck_completed(
                page_count=result.page_count,
                requires_confirmation=result.requires_confirmation,
            )
            return result
        except Exception as exc:
            reporter.precheck_failed(exc)
            raise

    def _inspect_and_validate(
        self,
        tender: TenderDocument,
        pdf_path: Path,
    ) -> PdfInspectionResult:
        inspection = self._pdf_inspector(
            pdf_path,
            max_pdf_mb=self._max_pdf_mb,
            max_pdf_pages=self._max_pdf_pages,
        )
        if inspection.document_sha256 != tender.file_hash:
            raise PdfExtractionError(
                "PDF hash does not match the selected tender manifest record"
            )
        return inspection

    def _warnings(self, page_count: int) -> list[str]:
        if page_count <= self._page_threshold:
            return []
        return [
            f"Document exceeds the {self._page_threshold}-page confirmation threshold"
        ]
