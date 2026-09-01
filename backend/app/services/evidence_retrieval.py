import json
from collections.abc import Sequence
from typing import Protocol

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import EvidenceRecord
from app.schemas import Evidence, EvidenceSearchHit, EvidenceType

"""Create embeddings and retrieve company evidence from the database"""


MAX_TOP_K = 50
EXCERPT_CHARACTERS = 240

class EvidenceNotFoundError(LookupError):
    """when a requested evidence ID does not exist"""


class EmbeddingClient(Protocol):
    """Defines the embedding boundary used by evidence retrieval"""

    def embed(self, texts: Sequence[str], *, model: str) -> list[list[float]]: ...


class OpenAIEmbeddingClient:
    """Converts OpenAI embedding responses into the format used by our search code"""

    def __init__(self, client: OpenAI) -> None:
        self._client = client

    def embed(self, texts: Sequence[str], *, model: str) -> list[list[float]]:
        
        response = self._client.embeddings.create(model=model, input=list(texts))
        ordered_items = sorted(response.data, key=lambda item: item.index)
        
        return [item.embedding for item in ordered_items]
    


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
    
    if len(vectors) != len(texts):
        raise ValueError("embedding response count does not match input count")
    if any(not vector for vector in vectors):
        raise ValueError("embedding response contains an empty vector")
    
    return vectors


def _short_excerpt(text: str) -> str:
    
    normalized = " ".join(text.split())
    if len(normalized) <= EXCERPT_CHARACTERS:
        return normalized
    
    #return with ellipsis
    return f"{normalized[: EXCERPT_CHARACTERS - 1].rstrip()}…" 


def _cosine_similarity(distance: float) -> float:
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


def _searchable_text(record: EvidenceRecord) -> str:
    """Return the narrative text or structured data used to search one evidence record"""

    if record.supporting_text is not None:
        return record.supporting_text
    if record.structured_value is not None:
        return json.dumps(record.structured_value, sort_keys=True)
    
    raise ValueError(f"evidence record {record.id} has no searchable content")


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

    # use limit for memory saving
    statement = (
        select(EvidenceRecord)
        .where(EvidenceRecord.embedding.is_(None))
        .order_by(EvidenceRecord.id)
        .limit(batch_size) 
    )

    embedded_count = 0
    while True:
        
        records = list(session.scalars(statement).all())
        if not records:
            break

        searchable_texts = [_searchable_text(record) for record in records]
        vectors = _embed(client, searchable_texts, model=model)
        for record, vector in zip(records, vectors, strict=True):
            record.embedding = vector

        # Persist this batch so the next query only returns remaining records
        session.flush()
        embedded_count += len(records)

    return embedded_count


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
        hits.append(
            EvidenceSearchHit(
                evidence_id=record.id,
                evidence_type=EvidenceType(record.evidence_type),
                supporting_excerpt=_short_excerpt(_searchable_text(record)),
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
