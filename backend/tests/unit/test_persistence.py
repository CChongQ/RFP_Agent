from datetime import date
from unittest.mock import Mock

from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import EvidenceRecord
from app.schemas import CompanyEvidenceSeed, Evidence, EvidenceType
from app.services.company_seed import seed_company_evidence

"""
Test database table registration and company evidence seeding.

Below use mocked session so these unit tests do not need PostgreSQL
"""

# Basic tests

def test_minimal_persistence_tables_are_registered() -> None:
    expected_tables = {
        "analysis_runs",
        "decisions",
        "evidence",
        "requirements",
        "tenders",
    }
    # Allow new tables without breaking this MVP table check.
    assert set(Base.metadata.tables).issuperset(expected_tables)


def test_company_evidence_seed_upserts_stable_ids() -> None:
    seed = CompanyEvidenceSeed(
        evidence=[
            Evidence(
                evidence_id="PROJECT-TEST-001",
                evidence_type=EvidenceType.PROJECT,
                supporting_text="A fictional implementation project",
                structured_value={"contract_value": 1000000},
                valid_from=date(2024, 1, 1),
                valid_until=date(2025, 1, 1),
            )
        ]
    )
    session = Mock(spec=Session)
    session.get.return_value = None

    count = seed_company_evidence(session, seed)

    assert count == 1
    session.add.assert_called_once()
    added_record = session.add.call_args.args[0]
    assert isinstance(added_record, EvidenceRecord)
    assert added_record.id == "PROJECT-TEST-001"
    session.flush.assert_called_once()


# Corner-case tests

def test_company_evidence_seed_clears_stale_embedding_after_text_change() -> None:
    existing_record = EvidenceRecord(
        id="PROJECT-TEST-001",
        evidence_type=EvidenceType.PROJECT.value,
        supporting_text="Old project text",
        embedding=[0.1, 0.2, 0.3],
    )
    seed = CompanyEvidenceSeed(
        evidence=[
            Evidence(
                evidence_id=existing_record.id,
                evidence_type=EvidenceType.PROJECT,
                supporting_text="Updated project text",
            )
        ]
    )
    session = Mock(spec=Session)
    session.get.return_value = existing_record

    seed_company_evidence(session, seed)

    assert existing_record.supporting_text == "Updated project text"
    assert existing_record.embedding is None
    session.add.assert_not_called()
