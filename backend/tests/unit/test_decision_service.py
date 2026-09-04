import json
from collections.abc import Sequence
from unittest.mock import Mock

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
from app.services.decision_service import DecisionService, OpenAIEvidenceAssessmentClient

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
    def __init__(
        self,
        assessment: EvidenceAssessment,
        *,
        retry_assessment: EvidenceAssessment | None = None,
    ) -> None:
        self._assessment = assessment
        self._retry_assessment = retry_assessment
        self.received_evidence: list[Evidence] = []
        self.rejected_evidence_ids: list[list[str]] = []

    def assess(
        self,
        *,
        model: str,
        requirement: Requirement,
        evidence: Sequence[Evidence],
        rejected_evidence_ids: Sequence[str] = (),
    ) -> EvidenceAssessment:
        assert model == "mock-model"
        self.received_evidence = list(evidence)
        rejected_ids = list(rejected_evidence_ids)
        self.rejected_evidence_ids.append(rejected_ids)
        if rejected_ids and self._retry_assessment is not None:
            return self._retry_assessment
        return self._assessment


class FixedRuleEvaluator:
    def __init__(self, result: DeterministicRuleResult | None) -> None:
        self._result = result

    def evaluate(
        self,
        requirement: Requirement,
    ) -> DeterministicRuleResult | None:
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
        rule_evaluator=FixedRuleEvaluator(None),
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


def test_decision_service_retries_invalid_evidence_id_once() -> None:
    evidence = _evidence()
    assessor = FakeAssessmentClient(
        EvidenceAssessment(
            status=DecisionStatus.SATISFIED,
            evidence_ids=["requirement_id"],
            reason="The requirement appears supported",
        ),
        retry_assessment=EvidenceAssessment(
            status=DecisionStatus.SATISFIED,
            evidence_ids=[evidence.evidence_id],
            reason="The stored project supports the requirement",
        ),
    )
    service = DecisionService(
        FakeEvidenceReader([evidence]),
        assessor,
        model="mock-model",
    )

    result = service.decide([_requirement()])

    assert result.decisions[0].status is DecisionStatus.SATISFIED
    assert result.decisions[0].evidence_ids == [evidence.evidence_id]
    assert assessor.rejected_evidence_ids == [[], ["requirement_id"]]


def test_decision_service_uses_human_review_after_invalid_retry() -> None:
    evidence = _evidence()
    assessor = FakeAssessmentClient(
        EvidenceAssessment(
            status=DecisionStatus.SATISFIED,
            evidence_ids=["requirement_id"],
            reason="The requirement appears supported",
        ),
        retry_assessment=EvidenceAssessment(
            status=DecisionStatus.SATISFIED,
            evidence_ids=[evidence.evidence_id, "still-invalid"],
            reason="The requirement appears supported",
        ),
    )
    service = DecisionService(
        FakeEvidenceReader([evidence]),
        assessor,
        model="mock-model",
    )

    result = service.decide([_requirement()])

    decision = result.decisions[0]
    assert decision.status is DecisionStatus.REQUIRES_HUMAN_REVIEW
    assert decision.evidence_ids == [evidence.evidence_id]
    assert result.overall_recommendation is OverallRecommendation.HUMAN_REVIEW
    assert assessor.rejected_evidence_ids == [[], ["requirement_id"]]


def test_openai_assessment_input_lists_allowed_and_rejected_ids() -> None:
    evidence = _evidence()
    openai_client = Mock()
    openai_client.responses.parse.return_value = Mock(
        output_parsed=EvidenceAssessment(
            status=DecisionStatus.SATISFIED,
            evidence_ids=[evidence.evidence_id],
            reason="The stored project supports the requirement",
        ),
        usage=None,
    )
    client = OpenAIEvidenceAssessmentClient(openai_client)

    client.assess(
        model="mock-model",
        requirement=_requirement(),
        evidence=[evidence],
        rejected_evidence_ids=["requirement_id"],
    )

    input_payload = json.loads(
        openai_client.responses.parse.call_args.kwargs["input"]
    )
    assert input_payload["allowed_evidence_ids"] == [evidence.evidence_id]
    assert input_payload["validation_feedback"]["rejected_evidence_ids"] == [
        "requirement_id"
    ]


def test_automatic_rule_failure_requires_human_review() -> None:
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

    assert result.decisions[0].status is DecisionStatus.REQUIRES_HUMAN_REVIEW
    assert result.decisions[0].rule_result == failed_rule
    assert result.overall_recommendation is OverallRecommendation.HUMAN_REVIEW


def test_rule_human_review_cannot_become_satisfied() -> None:
    evidence = _evidence()
    review_rule = DeterministicRuleResult(
        rule_type="minimum_value",
        outcome=RuleOutcome.REQUIRES_HUMAN_REVIEW,
        reason="The stored value is missing",
    )
    service = DecisionService(
        FakeEvidenceReader([evidence]),
        FakeAssessmentClient(
            EvidenceAssessment(
                status=DecisionStatus.SATISFIED,
                evidence_ids=[evidence.evidence_id],
                reason="The project appears relevant",
            )
        ),
        model="mock-model",
        rule_evaluator=FixedRuleEvaluator(review_rule),
    )

    result = service.decide([_requirement()])

    assert result.decisions[0].status is DecisionStatus.REQUIRES_HUMAN_REVIEW
    assert result.decisions[0].rule_result == review_rule


def test_passed_rule_allows_model_assessment() -> None:
    evidence = _evidence()
    passed_rule = DeterministicRuleResult(
        rule_type="minimum_count",
        outcome=RuleOutcome.PASSED,
        reason="The project count meets the minimum",
    )
    service = DecisionService(
        FakeEvidenceReader([evidence]),
        FakeAssessmentClient(
            EvidenceAssessment(
                status=DecisionStatus.SATISFIED,
                evidence_ids=[evidence.evidence_id],
                reason="The project demonstrates relevant experience",
            )
        ),
        model="mock-model",
        rule_evaluator=FixedRuleEvaluator(passed_rule),
    )

    result = service.decide([_requirement()])

    assert result.decisions[0].status is DecisionStatus.SATISFIED
    assert result.decisions[0].rule_result == passed_rule
