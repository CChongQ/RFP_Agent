from collections.abc import Sequence
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.database.models import EvidenceRecord
from app.schemas import EvidenceType
from app.services.evidence_retrieval import (
    EvidenceNotFoundError,
    embed_missing_evidence,
    get_evidence_by_id,
    search_company_evidence,
)

"""
Test embedding, searching, and loading stored company evidence.
"""

# Fake embeddings keep retrieval tests offline and dimension-independent
class FakeEmbeddingClient:
    """Returns synthetic vectors without making network calls"""

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = iter(vectors)
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts: Sequence[str], *, model: str) -> list[list[float]]:
        self.calls.append((list(texts), model))
        return [next(self._vectors) for _ in texts]


def _record(*, evidence_id: str = "PROJECT-TEST-001") -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        evidence_type=EvidenceType.PROJECT.value,
        supporting_text="A fictional team delivered a secure applicant tracking system",
        structured_value={"sector": "public"},
    )


# Basic tests 

def test_embed_missing_evidence_caches_vectors() -> None:
    record = _record()
    populated = Mock()
    populated.all.return_value = [record]
    empty = Mock()
    empty.all.return_value = []
    session = Mock(spec=Session)
    session.scalars.side_effect = [populated, empty]
    client = FakeEmbeddingClient([[0.1, 0.2, 0.3]])

    assert embed_missing_evidence(session, client, model="mock-embedding") == 1

    assert record.embedding == [0.1, 0.2, 0.3]
    assert client.calls == [([record.supporting_text], "mock-embedding")]
    session.flush.assert_called_once()


def test_embed_missing_evidence_supports_structured_data() -> None:
    # test evidence can be embedded even when it has no normal supporting_text and has only structured JSON data
    record = _record(evidence_id="CERTIFICATION-TEST-001")
    record.supporting_text = None
    record.structured_value = {"certification": "SOC 2"}

    populated = Mock()
    populated.all.return_value = [record]
    empty = Mock()
    empty.all.return_value = []
    session = Mock(spec=Session)
    session.scalars.side_effect = [populated, empty]
    client = FakeEmbeddingClient([[0.7, 0.8, 0.9]])

    assert embed_missing_evidence(session, client, model="mock-embedding") == 1

    assert client.calls == [(['{"certification": "SOC 2"}'], "mock-embedding")]
    assert record.embedding == [0.7, 0.8, 0.9]
    session.flush.assert_called_once()


def test_search_company_evidence_returns_ranked_excerpt() -> None:
    record = _record()
    result = Mock()
    result.all.return_value = [(record, 0.2)]
    session = Mock(spec=Session)
    session.execute.return_value = result
    client = FakeEmbeddingClient([[0.4, 0.5, 0.6]])

    hits = search_company_evidence(
        session,
        client,
        model="mock-embedding",
        query="applicant tracking system experience",
        evidence_type=EvidenceType.PROJECT,
        top_k=3,
        min_score=0.25,
    )

    assert hits[0].evidence_id == "PROJECT-TEST-001"
    assert hits[0].score == 0.8
    assert "applicant tracking system" in hits[0].supporting_excerpt
    assert client.calls == [(["applicant tracking system experience"], "mock-embedding")]


def test_get_evidence_by_id_returns_record() -> None:
    session = Mock(spec=Session)
    session.get.return_value = _record(evidence_id="CAPABILITY-TEST-001")

    evidence = get_evidence_by_id(session, "CAPABILITY-TEST-001")

    assert evidence.evidence_id == "CAPABILITY-TEST-001"


# Corner-case tests

def test_embed_missing_evidence_skips_when_no_records_need_vectors() -> None:
    empty = Mock()
    empty.all.return_value = []
    session = Mock(spec=Session)
    session.scalars.return_value = empty
    client = FakeEmbeddingClient([])

    assert embed_missing_evidence(session, client, model="mock-embedding") == 0
    assert client.calls == []
    session.flush.assert_not_called()


def test_search_company_evidence_rejects_invalid_score_limit() -> None:
    session = Mock(spec=Session)
    client = FakeEmbeddingClient([])

    with pytest.raises(ValueError, match="min_score"):
        search_company_evidence(
            session,
            client,
            model="mock-embedding",
            query="implementation experience",
            min_score=1.1,
        )


def test_get_evidence_by_id_rejects_missing_id() -> None:
    session = Mock(spec=Session)
    session.get.return_value = None

    with pytest.raises(EvidenceNotFoundError, match="MISSING-TEST-001"):
        get_evidence_by_id(session, "MISSING-TEST-001")
