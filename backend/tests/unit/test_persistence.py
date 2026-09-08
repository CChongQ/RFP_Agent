from datetime import date
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.database.models import EvidenceRecord
from app.schemas import CompanyEvidenceSeed, Evidence, EvidenceType
from app.services.company_seed import seed_company_evidence

"""
Test database table registration and company evidence seeding.

Below use mocked session so these unit tests do not need PostgreSQL
"""

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

@pytest.mark.parametrize(
    "supporting_text,new_value,clears_embedding",
    [
        (None, 2000000, True),
        (None, 1000000, False),
        ("A fictional implementation project", 2000000, False),
    ],
)
def test_seed_invalidates_embedding_only_when_searchable_content_changes(
    supporting_text: str | None,
    new_value: int,
    clears_embedding: bool,
) -> None:
    embedding = [0.1, 0.2, 0.3]
    existing_record = EvidenceRecord(
        id="PROJECT-TEST-001",
        evidence_type=EvidenceType.PROJECT.value,
        supporting_text=supporting_text,
        structured_value={"contract_value": 1000000},
        embedding=embedding,
    )
    seed = CompanyEvidenceSeed(
        evidence=[
            Evidence(
                evidence_id=existing_record.id,
                evidence_type=EvidenceType.PROJECT,
                supporting_text=supporting_text,
                structured_value={"contract_value": new_value},
            )
        ]
    )
    session = Mock(spec=Session)
    session.get.return_value = existing_record

    seed_company_evidence(session, seed)

    assert existing_record.structured_value == {"contract_value": new_value}
    assert existing_record.embedding == (None if clears_embedding else embedding)


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
