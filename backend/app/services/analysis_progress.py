import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from typing import Protocol, Self

from pydantic import JsonValue

PROGRESS_LOGGER_NAME = "app.analysis_progress"
PROGRESS_LOG_MAX_BYTES = 5 * 1024 * 1024
PROGRESS_LOG_BACKUP_COUNT = 3


class AnalysisStage(StrEnum):
    EVIDENCE_PREPARATION = "evidence_preparation"
    PDF_EXTRACTION = "pdf_extraction"
    REQUIREMENT_EXTRACTION = "requirement_extraction"
    DECISION = "decision"
    PERSISTENCE = "persistence"


class ProgressReporter(Protocol):
    def precheck_started(self) -> None: ...

    def precheck_completed(
        self,
        *,
        page_count: int,
        requires_confirmation: bool,
    ) -> None: ...

    def precheck_failed(self, error: Exception) -> None: ...

    def bind_analysis(self, analysis_id: str) -> Self: ...

    def analysis_started(self) -> None: ...

    def stage_started(self, stage: AnalysisStage, *, total: int | None = None) -> None: ...

    def stage_progress(
        self,
        stage: AnalysisStage,
        *,
        current: int,
        total: int,
    ) -> None: ...

    def stage_completed(
        self,
        stage: AnalysisStage,
        *,
        message: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None: ...

    def analysis_completed(self, *, recommendation: str) -> None: ...

    def analysis_failed(self, error: Exception) -> None: ...


ProgressReporterFactory = Callable[..., ProgressReporter]



def configure_analysis_progress_logging(log_path: Path) -> None:
    """Write structured analysis events to a rotating file only."""

    resolved_path = Path(log_path).resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(PROGRESS_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # prevent handler being added more than once 
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == resolved_path:
            return
        
    # remove existing handlers
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    handler = RotatingFileHandler(
        resolved_path,
        maxBytes=PROGRESS_LOG_MAX_BYTES,
        backupCount=PROGRESS_LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


# ========== Analysis event reporter ==========


@dataclass
class AnalysisEventReporter:
    """Own event schema and timing for one tender analysis"""

    tender_id: str
    analysis_id: str | None = None
    
    clock: Callable[[], float] = perf_counter
    
    _started_at: float | None = field(default=None, init=False)
    _active_stage: AnalysisStage | None = field(default=None, init=False)
    _stage_started_at: float | None = field(default=None, init=False)

    def _emit(
        self,
        *,
        event: str,
        message: str,
        stage: AnalysisStage | None = None,
        current: int | None = None,
        total: int | None = None,
        duration_ms: int | None = None,
        level: int = logging.INFO,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        
        record: dict[str, JsonValue] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": logging.getLevelName(level),
            "event": event,
            "message": message,
            "tender_id": self.tender_id,
        }
        
        optional_fields: tuple[tuple[str, JsonValue | None], ...] = (
            ("analysis_id", self.analysis_id),
            ("stage", stage.value if stage is not None else None),
            ("current", current),
            ("total", total),
            ("duration_ms", duration_ms),
        )
        reserved_fields = record.keys() | {key for key, _ in optional_fields}
        
        record.update(
            (key, value) for key, value in (details or {}).items() if key not in reserved_fields
        )
        record.update((key, value) for key, value in optional_fields if value is not None)
        
        logging.getLogger(PROGRESS_LOGGER_NAME).log(
            level,
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        )

    def precheck_started(self) -> None:
        self._started_at = self.clock()
        self._emit(
            event="precheck.started",
            message="PDF precheck started",
        )

    def precheck_completed(
        self,
        *,
        page_count: int,
        requires_confirmation: bool,
    ) -> None:
        
        self._emit(
            event="precheck.completed",
            message=f"PDF precheck completed: {page_count} pages",
            duration_ms=self._elapsed_ms(self._started_at),
            details={
                "page_count": page_count,
                "requires_confirmation": requires_confirmation,
            },
        )

    def precheck_failed(self, error: Exception) -> None:
        self._emit(
            event="precheck.failed",
            message="PDF precheck failed",
            duration_ms=self._elapsed_ms(self._started_at),
            level=logging.ERROR,
            details={"error_type": type(error).__name__},
        )

    def bind_analysis(self, analysis_id: str) -> Self:
        self.analysis_id = analysis_id
        return self

    def analysis_started(self) -> None:
        self._started_at = self.clock()
        self._emit(
            event="analysis.started",
            message="Analysis started",
        )

    def stage_started(self, stage: AnalysisStage, *, total: int | None = None) -> None:
        self._active_stage = stage
        self._stage_started_at = self.clock()
        self._emit(
            event="analysis.stage.started",
            message=f"{_stage_label(stage)} started",
            stage=stage,
            current=0 if total is not None else None,
            total=total,
        )

    def stage_progress(
        self,
        stage: AnalysisStage,
        *,
        current: int,
        total: int,
        
    ) -> None:
        started_at = self._stage_started_at if self._active_stage is stage else None
        
        self._emit(
            event="analysis.stage.progress",
            message=f"{_stage_label(stage)} in progress",
            stage=stage,
            current=current,
            total=total,
            duration_ms=self._elapsed_ms(started_at),
        )

    def stage_completed(
        self,
        stage: AnalysisStage,
        *,
        message: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        
        started_at = self._stage_started_at if self._active_stage is stage else None
        
        self._emit(
            event="analysis.stage.completed",
            message=message or f"{_stage_label(stage)} completed",
            stage=stage,
            duration_ms=self._elapsed_ms(started_at),
            details=details,
        )
        if self._active_stage is stage:
            self._active_stage = None
            self._stage_started_at = None

    def analysis_completed(self, *, recommendation: str) -> None:
        self._emit(
            event="analysis.completed",
            message=f"Analysis completed: {recommendation}",
            duration_ms=self._elapsed_ms(self._started_at),
            details={"recommendation": recommendation},
        )

    def analysis_failed(self, error: Exception) -> None:
        
        stage_label = (
            _stage_label(self._active_stage) if self._active_stage is not None else "analysis"
        )
        
        self._emit(
            event="analysis.failed",
            message=f"Analysis failed during {stage_label}",
            stage=self._active_stage,
            duration_ms=self._elapsed_ms(self._started_at),
            level=logging.ERROR,
            details={"error_type": type(error).__name__},
        )

    def _elapsed_ms(self, started_at: float | None) -> int | None:
        if started_at is None:
            return None
        return max(0, round((self.clock() - started_at) * 1000))


def _stage_label(stage: AnalysisStage) -> str:
    return stage.value.replace("_", " ").capitalize()
