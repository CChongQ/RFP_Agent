import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database.models import EvidenceRecord
from app.schemas import CompanyEvidenceSeed, Evidence


class CompanySeedError(ValueError):
    """when a company evidence seed file cannot be loaded"""


def _to_evidence_record(evidence: Evidence) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence.evidence_id,
        evidence_type=evidence.evidence_type.value,
        supporting_text=evidence.supporting_text,
        structured_value=evidence.structured_value,
        valid_from=evidence.valid_from,
        valid_until=evidence.valid_until,
    )


def _update_evidence_record(record: EvidenceRecord, evidence: Evidence) -> None:
    
    text_changed = record.supporting_text != evidence.supporting_text
    
    record.evidence_type = evidence.evidence_type.value
    record.supporting_text = evidence.supporting_text
    record.structured_value = evidence.structured_value
    record.valid_from = evidence.valid_from
    record.valid_until = evidence.valid_until
    
    if text_changed:
        # old vector no longer match the updated narrative text
        record.embedding = None


def load_company_seed(path: Path) -> CompanyEvidenceSeed:
    """Read and validate one consolidated company evidence JSON file"""

    seed_path = Path(path)
    if not seed_path.is_file():
        raise CompanySeedError(f"company seed file does not exist: {seed_path}")

    # Validate the whole file before writing any evidence rows
    try:
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        
        return CompanyEvidenceSeed.model_validate(payload)
    
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise CompanySeedError(f"invalid company seed file: {seed_path.name}") from exc


def seed_company_evidence(session: Session, seed: CompanyEvidenceSeed) -> int:
    
    """Upsert company evidence by ID"""

    for evidence in seed.evidence:
        record = session.get(EvidenceRecord, evidence.evidence_id)
        
        if record is None:
            #insert
            session.add(_to_evidence_record(evidence))
        else:
            #update 
            _update_evidence_record(record, evidence)
            
    # Flush for immediate validation while the caller still owns the transaction
    session.flush()
    
    return len(seed.evidence)
