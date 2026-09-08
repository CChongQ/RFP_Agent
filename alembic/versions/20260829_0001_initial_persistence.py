"""Create the initial database schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260908_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the tables used by the analysis workflow."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenders",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("local_filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenders")),
        sa.UniqueConstraint("sha256", name=op.f("uq_tenders_sha256")),
    )

    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("supporting_text", sa.Text(), nullable=True),
        sa.Column(
            "structured_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "supporting_text IS NOT NULL OR structured_value IS NOT NULL",
            name=op.f("ck_evidence_content_present"),
        ),
        sa.CheckConstraint(
            "evidence_type IN "
            "('company_profile','project','certification','capability','policy')",
            name=op.f("ck_evidence_evidence_type"),
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from",
            name=op.f("ck_evidence_valid_date_order"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
    )
    op.create_index(op.f("ix_evidence_evidence_type"), "evidence", ["evidence_type"])

    op.create_table(
        "requirements",
        sa.Column("id", sa.String(length=140), nullable=False),
        sa.Column("tender_id", sa.String(length=100), nullable=False),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("normalized_requirement", sa.Text(), nullable=False),
        sa.Column("requirement_type", sa.String(length=20), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "requirement_type IN ('mandatory','scored','optional','informational')",
            name=op.f("ck_requirements_requirement_type"),
        ),
        sa.CheckConstraint(
            "source_page >= 1",
            name=op.f("ck_requirements_source_page_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name=op.f("fk_requirements_tender_id_tenders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirements")),
    )
    op.create_index("idx_requirements_tender_id", "requirements", ["tender_id"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("tender_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("overall_recommendation", sa.String(length=20), nullable=True),
        sa.Column(
            "trace",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "overall_recommendation IS NULL OR "
            "overall_recommendation IN ('bid','no_bid','human_review')",
            name=op.f("ck_analysis_runs_overall_recommendation"),
        ),
        sa.CheckConstraint(
            "status IN ('running','completed','failed')",
            name=op.f("ck_analysis_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name=op.f("fk_analysis_runs_tender_id_tenders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_runs")),
    )
    op.create_index("idx_analysis_runs_tender_id", "analysis_runs", ["tender_id"])

    op.create_table(
        "decisions",
        sa.Column("analysis_id", sa.String(length=100), nullable=False),
        sa.Column("requirement_id", sa.String(length=140), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "rule_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN "
            "('satisfied','partially_satisfied','not_satisfied',"
            "'insufficient_evidence','requires_human_review')",
            name=op.f("ck_decisions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["analysis_runs.id"],
            name=op.f("fk_decisions_analysis_id_analysis_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name=op.f("fk_decisions_requirement_id_requirements"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "analysis_id",
            "requirement_id",
            name=op.f("pk_decisions"),
        ),
    )
    op.create_index("idx_decisions_requirement_id", "decisions", ["requirement_id"])


def downgrade() -> None:
    """Remove the schema in reverse dependency order."""

    op.drop_index("idx_decisions_requirement_id", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("idx_analysis_runs_tender_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index("idx_requirements_tender_id", table_name="requirements")
    op.drop_table("requirements")
    op.drop_index(op.f("ix_evidence_evidence_type"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_table("tenders")
