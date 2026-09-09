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
        requirement = RequirementRecord(
            analysis_id=analysis.id,
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
        assert session.get(RequirementRecord, (analysis.id, requirement.id)) is not None
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


@pytest.mark.integration
def test_requirements_are_kept_for_each_analysis_run() -> None:
    engine = create_database_engine()
    
    session = Session(engine)
    transaction = session.begin()

    try:
        tender = TenderRecord(
            id="TENDER-HISTORY-001",
            title="Synthetic History Tender",
            source_url="https://example.com/tenders/history",
            local_filename="history.pdf",
            sha256="B" * 64,
        )
        analyses = [
            AnalysisRunRecord(
                id=f"ANALYSIS-HISTORY-00{number}",
                tender_id=tender.id,
                status="completed",
                document_sha256=tender.sha256,
                model_version="mock-model",
                prompt_version="test-v1",
                overall_recommendation="bid",
                trace={"run": number},
                evidence_snapshot=[{"evidence_id": f"EVIDENCE-{number}"}],
                run_settings={"max_chunk_characters": number * 1_000},
            )
            for number in (1, 2)
        ]
        
        requirement_id = "TENDER-HISTORY-001-REQ-001"
        requirements = [
            RequirementRecord(
                analysis_id=analysis.id,
                id=requirement_id,
                tender_id=tender.id,
                requirement_text=f"Requirement text from run {number}",
                normalized_requirement="Provide implementation services",
                requirement_type="mandatory",
                source_page=1,
                source_excerpt=f"Requirement text from run {number}",
                source_references=[],
                rules=[{"rule_id": f"RULE-{number}"}],
                requires_human_review=False,
            )
            for number, analysis in enumerate(analyses, start=1)
        ]
        
        decisions = [
            DecisionRecord(
                analysis_id=analysis.id,
                requirement_id=requirement_id,
                status="satisfied",
                evidence_ids=[],
                reason=f"Decision from run {number}",
            )
            for number, analysis in enumerate(analyses, start=1)
        ]

        session.add(tender)
        session.flush()

        session.add_all(analyses)
        session.flush()

        session.add_all(requirements)
        session.flush()

        session.add_all(decisions)
        session.flush()

        saved_requirements = session.scalars(
            select(RequirementRecord)
            .where(RequirementRecord.id == requirement_id)
            .order_by(RequirementRecord.analysis_id)
        ).all()

        assert [item.requirement_text for item in saved_requirements] == [
            "Requirement text from run 1",
            "Requirement text from run 2",
        ]
        assert [item.rules for item in saved_requirements] == [
            [{"rule_id": "RULE-1"}],
            [{"rule_id": "RULE-2"}],
        ]
        assert analyses[1].evidence_snapshot == [{"evidence_id": "EVIDENCE-2"}]
        assert analyses[1].run_settings == {"max_chunk_characters": 2_000}
    finally:
        transaction.rollback()
        session.close()
        engine.dispose()
