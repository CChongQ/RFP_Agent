from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.schemas.enums import (
    DecisionStatus,
    EvidenceType,
    OverallRecommendation,
    RequirementType,
)

ANALYSIS_STATUS_RUNNING = "running"
ANALYSIS_STATUS_COMPLETED = "completed"
ANALYSIS_STATUS_FAILED = "failed"
ANALYSIS_RUN_STATUSES = (
    ANALYSIS_STATUS_RUNNING,
    ANALYSIS_STATUS_COMPLETED,
    ANALYSIS_STATUS_FAILED,
)


def _sql_string_list(values: Sequence[str]) -> str:
    """Format trusted enum values for SQL check constraints"""

    return ",".join(f"'{value}'" for value in values)


REQUIREMENT_TYPE_VALUES = _sql_string_list([item.value for item in RequirementType])
EVIDENCE_TYPE_VALUES = _sql_string_list([item.value for item in EvidenceType])
DECISION_STATUS_VALUES = _sql_string_list([item.value for item in DecisionStatus])
RECOMMENDATION_VALUES = _sql_string_list(
    [item.value for item in OverallRecommendation]
)
ANALYSIS_STATUS_VALUES = _sql_string_list(ANALYSIS_RUN_STATUSES)


class TenderRecord(Base):
    """Stores one immutable tender source record"""

    __tablename__ = "tenders"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    
    #base info
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    local_filename: Mapped[str] = mapped_column(String(255))
    
    #for timeline 
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    
    #a unique hash prevents the same source document from being registered twice
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    


class RequirementRecord(Base):
    """Stores a page-traceable requirement extracted from a tender"""

    __tablename__ = "requirements"
    
    __table_args__ = (
        CheckConstraint(
            f"requirement_type IN ({REQUIREMENT_TYPE_VALUES})", name="requirement_type",
        ),
        CheckConstraint("source_page >= 1", name="source_page_positive"),
        Index("idx_requirements_tender_id", "tender_id"),
    )
    
    id: Mapped[str] = mapped_column(String(140), primary_key=True)
    tender_id: Mapped[str] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    requirement_text: Mapped[str] = mapped_column(Text)
    normalized_requirement: Mapped[str] = mapped_column(Text)
    requirement_type: Mapped[str] = mapped_column(String(20))
    
    #page and excerpt keeps a direct path back to the tender text
    source_page: Mapped[int] = mapped_column(Integer)
    source_excerpt: Mapped[str] = mapped_column(Text)
    
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvidenceRecord(Base):
    """Stores company evidence used to evaluate tender requirements"""

    __tablename__ = "evidence"
    
    __table_args__ = (
        CheckConstraint(
            f"evidence_type IN ({EVIDENCE_TYPE_VALUES})",
            name="evidence_type",
        ),
        CheckConstraint(
            "supporting_text IS NOT NULL OR structured_value IS NOT NULL",
            name="content_present",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from",
            name="valid_date_order",
        ),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    evidence_type: Mapped[str] = mapped_column(String(32), index=True)
    
    # narrative text supports semantic search, structured JSON supports exact checks
    supporting_text: Mapped[str | None] = mapped_column(Text)
    structured_value: Mapped[Any | None] = mapped_column(JSONB)
    
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    
    # Store embeddings for repeated analyses 
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnalysisRunRecord(Base):
    """Stores the state and trace summary for one analysis run"""

    __tablename__ = "analysis_runs"
    
    __table_args__ = (
        CheckConstraint(f"status IN ({ANALYSIS_STATUS_VALUES})", name="status"),
        CheckConstraint(
            "overall_recommendation IS NULL OR "
            f"overall_recommendation IN ({RECOMMENDATION_VALUES})",
            name="overall_recommendation",
        ),
        Index("idx_analysis_runs_tender_id", "tender_id"),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), nullable=False)
    
    status: Mapped[str] = mapped_column(String(20), default=ANALYSIS_STATUS_RUNNING)
    model_version: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    overall_recommendation: Mapped[str | None] = mapped_column(String(20))
    
    #flexible trace JSON avoids separate tables for small evolving metadata
    trace: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    
    document_sha256: Mapped[str] = mapped_column(String(64))
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DecisionRecord(Base):
    """Stores one requirement decision within an analysis run"""

    __tablename__ = "decisions"
    
    __table_args__ = (
        CheckConstraint(
            f"status IN ({DECISION_STATUS_VALUES})",
            name="status",
        ),
        Index("idx_decisions_requirement_id", "requirement_id"),
    )

    # for dup check: composite key (analysis, requirement) allows one decision per requirement in each run
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True
    )
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), primary_key=True
    )
    
    status: Mapped[str] = mapped_column(String(32))
    evidence_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    reason: Mapped[str] = mapped_column(Text)
    rule_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
