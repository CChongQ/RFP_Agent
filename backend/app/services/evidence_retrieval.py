import json
from collections.abc import Sequence
from typing import Protocol

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import EvidenceRecord
from app.schemas import Evidence, EvidenceSearchHit, EvidenceType

MAX_TOP_K = 50
EXCERPT_CHARACTERS = 240


class EvidenceNotFoundError(LookupError):
    """when a requested evidence ID does not exist"""


class EmbeddingClient(Protocol):
    """Defines the embedding boundary used by evidence retrieval"""

    def embed(self, texts: Sequence[str], *, model: str) -> list[list[float]]: ...


class OpenAIEmbeddingClient:
    """Converts OpenAI embedding responses into the format used by our search code."""

    def __init__(self, client: OpenAI) -> None:
        self._client = client

    def embed(self, texts: Sequence[str], *, model: str) -> list[list[float]]:
        
        response = self._client.embeddings.create(model=model, input=list(texts))
        
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


def _validate_model(model: str) -> str:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model cannot be empty")
    return model.strip()


def _embed(
    client: EmbeddingClient,
    texts: Sequence[str],
    *,
    model: str,
) -> list[list[float]]:
    vectors = client.embed(texts, model=_validate_model(model))
    # Reject incomplete output before vectors can attach to the wrong evidence rows
    if len(vectors) != len(texts):
        raise ValueError("embedding response count does not match input count")
    if any(not vector for vector in vectors):
        raise ValueError("embedding response contains an empty vector")
    return vectors


def _short_excerpt(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= EXCERPT_CHARACTERS:
        return normalized
    return f"{normalized[: EXCERPT_CHARACTERS - 1].rstrip()}…"


def _cosine_similarity(distance: float) -> float:
    # Small database rounding errors should not leave the valid -1 to 1 range.
    return max(-1.0, min(1.0, 1.0 - distance))


def _to_evidence(record: EvidenceRecord) -> Evidence:
    return Evidence(
        evidence_id=record.id,
        evidence_type=EvidenceType(record.evidence_type),
        supporting_text=record.supporting_text,
        structured_value=record.structured_value,
        valid_from=record.valid_from,
        valid_until=record.valid_until,
    )


def _validate_batch_size(batch_size: int) -> None:
    #corner case: use bool value for batch_size
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")


def _validate_top_k(top_k: int) -> None:
    if (
        not isinstance(top_k, int)
        or isinstance(top_k, bool)
        or not 1 <= top_k <= MAX_TOP_K
    ):
        raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}")


def _validate_min_score(min_score: float) -> None:
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)):
        raise ValueError("min_score must be numeric")
    if not -1.0 <= min_score <= 1.0:
        raise ValueError("min_score must be between -1 and 1")


def embed_missing_evidence(
    session: Session,
    client: EmbeddingClient,
    *,
    model: str,
    batch_size: int = 100,
) -> int:
    """Create and save vectors for evidence text that has not been embedded yet"""

    _validate_batch_size(batch_size)

    # Embed only narrative rows without vectors to avoid repeat cost
    statement = (
        select(EvidenceRecord)
        .where(EvidenceRecord.supporting_text.is_not(None))
        .where(EvidenceRecord.embedding.is_(None))
        .order_by(EvidenceRecord.id)
    )
    records = list(session.scalars(statement).all())

    for start in range(0, len(records), batch_size):
        # Batch uncached text while preserving database row order
        record_batch = records[start : start + batch_size]
        
        narrative_texts = [
            record.supporting_text
            for record in record_batch
            if record.supporting_text is not None
        ]
        vectors = _embed(client, narrative_texts, model=model)
        for record, vector in zip(record_batch, vectors, strict=True):
            record.embedding = vector

    if records:
        session.flush()
    return len(records)


def search_company_evidence(
    session: Session,
    client: EmbeddingClient,
    *,
    model: str,
    query: str,
    evidence_type: EvidenceType | str | None = None,
    top_k: int = 5,
    min_score: float = -1.0,  # Minimum similarity score
) -> list[EvidenceSearchHit]:
    """Search cached evidence vectors and return short traceable matches"""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query cannot be empty")
    
    _validate_top_k(top_k)
    _validate_min_score(min_score)

    evidence_type_filter = EvidenceType(evidence_type) if evidence_type is not None else None
    
    # embed input query
    query_vector = _embed(client, [query.strip()], model=model)[0]
    
    # Build statement
    distance = EvidenceRecord.embedding.cosine_distance(query_vector)
    statement = select(EvidenceRecord, distance.label("distance")).where(
        EvidenceRecord.embedding.is_not(None)
    )
    #convert the min sim score into distance
    statement = statement.where(distance <= 1.0 - min_score)
    if evidence_type_filter is not None:
        statement = statement.where(
            EvidenceRecord.evidence_type == evidence_type_filter.value
        )
    statement = statement.order_by(distance).limit(top_k) #top-k distance 

    hits: list[EvidenceSearchHit] = []
    for record, distance_value in session.execute(statement).all():
        
        source_text = record.supporting_text
        if source_text is None:
            # Use structured data when normal text is unavailable
            source_text = json.dumps(record.structured_value, sort_keys=True)
            
        hits.append(
            EvidenceSearchHit(
                evidence_id=record.id,
                evidence_type=EvidenceType(record.evidence_type),
                supporting_excerpt=_short_excerpt(source_text),
                score=_cosine_similarity(float(distance_value)),
            )
        )
    return hits


def get_evidence_by_id(session: Session, evidence_id: str) -> Evidence:
    """Return one evidence record by ID"""

    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValueError("evidence_id cannot be empty")

    record = session.get(EvidenceRecord, evidence_id.strip())
    if record is None:
        raise EvidenceNotFoundError(f"evidence not found: {evidence_id.strip()}")
    
    return _to_evidence(record)
