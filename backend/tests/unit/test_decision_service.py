from collections.abc import Sequence

from app.schemas import (
    DecisionStatus,
    DeterministicRuleResult,
    Evidence,
    EvidenceAssessment,
    EvidenceSearchHit,
    EvidenceType,
    OverallRecommendation,
    Requirement,
    RequirementType,
    RuleOutcome,
    SourceReference,
)
from app.services.decision_service import DecisionService

"""
Test how evidence and rules become bid decisions
"""

# Fake retrieval and assessment isolate application-owned decision policy
class FakeEvidenceReader:
    def __init__(self, evidence: list[Evidence]) -> None:
        self._evidence = {item.evidence_id: item for item in evidence}

    def search(self, query: str, *, top_k: int) -> list[EvidenceSearchHit]:
        return [
            EvidenceSearchHit(
                evidence_id=item.evidence_id,
                evidence_type=item.evidence_type,
                supporting_excerpt=item.supporting_text or "Structured evidence",
                score=0.9,
            )
            for item in list(self._evidence.values())[:top_k]
        ]

    def get_by_id(self, evidence_id: str) -> Evidence:
        return self._evidence[evidence_id]


class FakeAssessmentClient:
    def __init__(self, assessment: EvidenceAssessment) -> None:
        self._assessment = assessment
        self.received_evidence: list[Evidence] = []

    def assess(
        self,
        *,
        model: str,
        requirement: Requirement,
        evidence: Sequence[Evidence],
    ) -> EvidenceAssessment:
        assert model == "mock-model"
        self.received_evidence = list(evidence)
        return self._assessment


class FixedRuleEvaluator:
    def __init__(self, result: DeterministicRuleResult) -> None:
        self._result = result

    def evaluate(
        self,
        requirement: Requirement,
        evidence: Sequence[Evidence],
    ) -> DeterministicRuleResult:
        return self._result


def _requirement() -> Requirement:
    return Requirement(
        requirement_id="TENDER-TEST-001-REQ-001",
        tender_id="TENDER-TEST-001",
        requirement_text="The bidder must demonstrate relevant implementation experience",
        normalized_requirement="Demonstrate relevant implementation experience",
        requirement_type=RequirementType.MANDATORY,
        source_page=4,
        source_excerpt="The bidder must demonstrate relevant implementation experience",
        source_references=[
            SourceReference(
                block_id="P004-B001",
                page_number=4,
                bounding_box=(72.0, 72.0, 500.0, 100.0),
            )
        ],
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="PROJECT-TEST-001",
        evidence_type=EvidenceType.PROJECT,
        supporting_text="A fictional team delivered an applicant tracking implementation",
    )


# Basic tests
def test_decision_service_returns_bid_for_supported_mandatory_requirement() -> None:
    evidence = _evidence()
    assessor = FakeAssessmentClient(
        EvidenceAssessment(
            status=DecisionStatus.SATISFIED,
            evidence_ids=[evidence.evidence_id],
            reason="The project demonstrates relevant implementation experience",
        )
    )
    service = DecisionService(
        FakeEvidenceReader([evidence]),
        assessor,
        model="mock-model",
    )

    result = service.decide([_requirement()])

    assert result.decisions[0].status is DecisionStatus.SATISFIED
    assert result.overall_recommendation is OverallRecommendation.BID
    assert assessor.received_evidence == [evidence]


# Corner-case tests
def test_decision_service_rejects_unsupported_satisfaction() -> None:
    evidence = _evidence()
    service = DecisionService(
        FakeEvidenceReader([evidence]),
        FakeAssessmentClient(
            EvidenceAssessment(
                status=DecisionStatus.SATISFIED,
                reason="The requirement appears supported",
            )
        ),
        model="mock-model",
    )

    result = service.decide([_requirement()])

    assert result.decisions[0].status is DecisionStatus.INSUFFICIENT_EVIDENCE
    assert result.decisions[0].evidence_ids == []
    assert result.overall_recommendation is OverallRecommendation.HUMAN_REVIEW


def test_deterministic_failure_overrides_model_and_produces_no_bid() -> None:
    evidence = _evidence()
    failed_rule = DeterministicRuleResult(
        rule_type="minimum_count",
        outcome=RuleOutcome.FAILED,
        reason="Reference project count 1 is below minimum 3",
    )
    service = DecisionService(
        FakeEvidenceReader([evidence]),
        FakeAssessmentClient(
            EvidenceAssessment(
                status=DecisionStatus.SATISFIED,
                evidence_ids=[evidence.evidence_id],
                reason="The project is relevant",
            )
        ),
        model="mock-model",
        rule_evaluator=FixedRuleEvaluator(failed_rule),
    )

    result = service.decide([_requirement()])

    assert result.decisions[0].status is DecisionStatus.NOT_SATISFIED
    assert result.decisions[0].rule_result == failed_rule
    assert result.overall_recommendation is OverallRecommendation.NO_BID
