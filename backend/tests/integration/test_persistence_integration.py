import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import (
    AnalysisRunRecord,
    DecisionRecord,
    EvidenceRecord,
    RequirementRecord,
    TenderRecord,
)
from app.database.session import create_database_engine
from app.schemas import CompanyEvidenceSeed, Evidence, EvidenceType
from app.services.company_seed import seed_company_evidence

"""
Test saving related analysis records in PostgreSQL.

"""

def _seed() -> CompanyEvidenceSeed:
    return CompanyEvidenceSeed(
        evidence=[
            Evidence(
                evidence_id="PROJECT-INTEGRATION-001",
                evidence_type=EvidenceType.PROJECT,
                supporting_text="A fictional integration-test project",
            )
        ]
    )


# Basic tests

@pytest.mark.integration
def test_seed_and_analysis_records_can_be_saved() -> None:
    engine = create_database_engine()
    session = Session(engine)
    transaction = session.begin()

    try:
        seed_company_evidence(session, _seed())

        tender = TenderRecord(
            id="TENDER-INTEGRATION-001",
            title="Synthetic Integration Tender",
            source_url="https://example.com/tenders/integration",
            local_filename="integration.pdf",
            sha256="A" * 64,
        )
        requirement = RequirementRecord(
            id="TENDER-INTEGRATION-001-REQ-001",
            tender_id=tender.id,
            requirement_text="The bidder must provide implementation services",
            normalized_requirement="Provide implementation services",
            requirement_type="mandatory",
            source_page=1,
            source_excerpt="The bidder must provide implementation services",
            source_references=[
                {
                    "block_id": "P001-B001",
                    "page_number": 1,
                    "bounding_box": [72.0, 72.0, 500.0, 100.0],
                }
            ],
            requires_human_review=False,
        )
        analysis = AnalysisRunRecord(
            id="ANALYSIS-INTEGRATION-001",
            tender_id=tender.id,
            status="completed",
            document_sha256=tender.sha256,
            model_version="mock-model",
            prompt_version="test-v1",
            overall_recommendation="bid",
            trace={"test": True},
        )
        decision = DecisionRecord(
            analysis_id=analysis.id,
            requirement_id=requirement.id,
            status="satisfied",
            evidence_ids=["PROJECT-INTEGRATION-001"],
            reason="Synthetic evidence supports the requirement",
        )
        
        # Save the tender first because requirements and analyses reference it
        session.add(tender)
        session.flush()

        # Save both decision parents before inserting the decision
        session.add_all([requirement, analysis])
        session.flush()

        session.add(decision)
        session.flush()

        evidence_count = session.scalar(
            select(func.count()).select_from(EvidenceRecord).where(
                EvidenceRecord.id == "PROJECT-INTEGRATION-001"
            )
        )
        assert evidence_count == 1
        assert session.get(DecisionRecord, (analysis.id, requirement.id)) is not None
    finally:
        transaction.rollback()
        session.close()
        engine.dispose()


# Corner-case tests

@pytest.mark.integration
def test_reseeding_evidence_keeps_one_record() -> None:
    engine = create_database_engine()
    session = Session(engine)
    transaction = session.begin()

    try:
        seed = _seed()

        seed_company_evidence(session, seed)
        seed_company_evidence(session, seed)

        evidence_count = session.scalar(
            select(func.count()).select_from(EvidenceRecord).where(
                EvidenceRecord.id == "PROJECT-INTEGRATION-001"
            )
        )

        assert evidence_count == 1
    finally:
        
        # note: keep integration runs repeatable and leave no test data.
        transaction.rollback()
        session.close()
        engine.dispose()
