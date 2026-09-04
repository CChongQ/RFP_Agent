import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI
from sqlalchemy.orm import Session

from app.prompts import EVIDENCE_ASSESSMENT_PROMPT
from app.schemas import (
    Decision,
    DecisionStatus,
    DeterministicRuleResult,
    Evidence,
    EvidenceAssessment,
    EvidenceSearchHit,
    OverallRecommendation,
    Requirement,
    RequirementType,
    RuleOutcome,
    ToolCallTrace,
)
from app.services.analysis_progress import AnalysisStage, ProgressReporter
from app.services.evidence_retrieval import (
    MAX_TOP_K,
    EmbeddingClient,
    get_evidence_by_id,
    search_company_evidence,
)
from app.services.model_usage import ModelUsageTracker

"""Build evidence-backed requirement decisions and an overall bid recommendation."""

logger = logging.getLogger(__name__)

INVALID_EVIDENCE_REVIEW_REASON = (
    "The model selected evidence outside the retrieved candidate set; "
    "manual review is required"
)



MANDATORY_REVIEW_STATUSES = frozenset({
    DecisionStatus.PARTIALLY_SATISFIED,
    DecisionStatus.INSUFFICIENT_EVIDENCE,
    DecisionStatus.REQUIRES_HUMAN_REVIEW,
})


class DecisionServiceError(RuntimeError):
    """when a decision cannot be provided safely"""


class EvidenceReader(Protocol):
    """Defines what methods an evidence reader must provide"""

    def search(self, query: str, *, top_k: int) -> list[EvidenceSearchHit]:
        """Find the best evidence matches for a query"""

        ...

    def get_by_id(self, evidence_id: str) -> Evidence:
        """Load one complete evidence record by ID"""

        ...


class EvidenceAssessmentClient(Protocol):
    """Defines the model boundary for evidence assessment"""

    def assess(
        self,
        *,
        model: str,
        requirement: Requirement,
        evidence: Sequence[Evidence],
        rejected_evidence_ids: Sequence[str] = (),
    ) -> EvidenceAssessment:
        """Return a model assessment for one requirement and its evidence"""

        ...


class RuleEvaluator(Protocol):
    """Defines deterministic evaluation for rules attached to a requirement"""

    def evaluate(
        self,
        requirement: Requirement,
    ) -> DeterministicRuleResult | None:
        """Run the requirement's validated rules when present"""

        ...


@dataclass(frozen=True)
class DecisionServiceResult:
    """Returns decisions with their application-owned recommendation"""

    decisions: list[Decision]
    overall_recommendation: OverallRecommendation
    tool_calls: list[ToolCallTrace]


class StoredEvidenceReader:
    """Connects the decision service to PostgreSQL evidence retrieval"""

    def __init__(
        self,
        session: Session,
        embedding_client: EmbeddingClient,
        *,
        embedding_model: str,
        min_score: float = -1.0,
    ) -> None:
        """Store the database and embedding settings used for evidence lookup"""

        if not embedding_model.strip():
            raise ValueError("embedding_model cannot be empty")
        self._session = session
        self._embedding_client = embedding_client
        self._embedding_model = embedding_model
        self._min_score = min_score

    def search(self, query: str, *, top_k: int) -> list[EvidenceSearchHit]:
        """Search saved evidence vectors for the closest matches"""

        return search_company_evidence(
            self._session,
            self._embedding_client,
            model=self._embedding_model,
            query=query,
            top_k=top_k,
            min_score=self._min_score,
        )

    def get_by_id(self, evidence_id: str) -> Evidence:
        """Load one full evidence record from the database"""

        return get_evidence_by_id(self._session, evidence_id)


class OpenAIEvidenceAssessmentClient:
    """Uses OpenAI structured output to assess company evidence"""

    def __init__(
        self,
        client: OpenAI,
        usage_tracker: ModelUsageTracker | None = None,
    ) -> None:
        """Store the OpenAI client and optional token counter"""

        self._client = client
        self._usage_tracker = usage_tracker

    def assess(
        self,
        *,
        model: str,
        requirement: Requirement,
        evidence: Sequence[Evidence],
        rejected_evidence_ids: Sequence[str] = (),
    ) -> EvidenceAssessment:
        """Ask OpenAI to assess how well the evidence supports the requirement"""

        input_text = _build_assessment_input(
            requirement,
            evidence,
            rejected_evidence_ids=rejected_evidence_ids,
        )
        
        response = self._client.responses.parse(
            model=model,
            instructions=EVIDENCE_ASSESSMENT_PROMPT.instructions,
            input=input_text,
            text_format=EvidenceAssessment,
            store=False,
        )
        
        # Add this model call's token counts to the analysis total
        if self._usage_tracker is not None and response.usage is not None:
            self._usage_tracker.add(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
            
        if response.output_parsed is None:
            raise DecisionServiceError("model response did not contain an evidence assessment")
        
        return response.output_parsed


class DecisionService:
    """Builds evidence-backed decisions and enforces final recommendation policy"""

    def __init__(
        self,
        evidence_reader: EvidenceReader,
        assessment_client: EvidenceAssessmentClient,
        *,
        model: str,
        rule_evaluator: RuleEvaluator | None = None,
        top_k: int = 5,
    ) -> None:
        if not model.strip():
            raise ValueError("model cannot be empty")
        
        if (
            not isinstance(top_k, int)
            or isinstance(top_k, bool)
            or not 1 <= top_k <= MAX_TOP_K
        ):
            raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}")
        
        self._evidence_reader = evidence_reader
        self._assessment_client = assessment_client
        self._model = model
        self._rule_evaluator = rule_evaluator
        self._top_k = top_k

    def decide(
        self,
        requirements: Sequence[Requirement],
        *,
        progress_reporter: ProgressReporter | None = None,
    ) -> DecisionServiceResult:
        """Create one decision per requirement and an overall recommendation"""

        if not requirements:
            raise ValueError("at least one requirement is required")
        
        requirement_ids = [requirement.requirement_id for requirement in requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement IDs must be unique")

        decisions: list[Decision] = []
        tool_calls: list[ToolCallTrace] = []
        
        # Process requirements separately so every decision remains traceable
        total_requirements = len(requirements)
        
        if progress_reporter is not None:
            progress_reporter.stage_started(
                AnalysisStage.DECISION,
                total=total_requirements,
            )
            
        for requirement_number, requirement in enumerate(requirements, start=1):
            decision, requirement_tool_calls = self._decide_requirement(requirement)
            decisions.append(decision)
            tool_calls.extend(requirement_tool_calls)
            
            if progress_reporter is not None and _should_report_progress(
                requirement_number,
                total_requirements,
            ):
                progress_reporter.stage_progress(
                    AnalysisStage.DECISION,
                    current=requirement_number,
                    total=total_requirements,
                )
            
        recommendation = _recommend(requirements, decisions)
      
        if progress_reporter is not None:
            progress_reporter.stage_completed(AnalysisStage.DECISION)
        return DecisionServiceResult(
            decisions=decisions,
            overall_recommendation=recommendation,
            tool_calls=tool_calls,
        )

    def _decide_requirement(
        self,
        requirement: Requirement,
    ) -> tuple[Decision, list[ToolCallTrace]]:
        """Run retrieval, rule checks, and assessment for one requirement"""

        hits, search_trace = self._search_evidence(requirement)
        
        tool_calls = [search_trace]
        
        # Load complete records because search hits contain only preview excerpts
        evidence, get_tool_calls = self._load_evidence(requirement, hits)
        
        tool_calls.extend(get_tool_calls)
        rule_result = self._evaluate_rule(requirement)
        assessment = self._assess_evidence(requirement, evidence)
        
        decision = _enforce_decision_policy(
            requirement,
            assessment,
            rule_result,
        )
        return decision, tool_calls

    def _search_evidence(
        self,
        requirement: Requirement,
    ) -> tuple[list[EvidenceSearchHit], ToolCallTrace]:
        """Search company evidence and record what the search returned"""

        query = requirement.normalized_requirement
        
        hits = self._evidence_reader.search(query, top_k=self._top_k)
        
        trace = ToolCallTrace(
            requirement_id=requirement.requirement_id,
            tool_name="search_company_evidence",
            arguments={"query": query, "evidence_type": None, "top_k": self._top_k},
            result_ids=[hit.evidence_id for hit in hits],
            scores=[hit.score for hit in hits],
        )
        return hits, trace

    def _evaluate_rule(
        self,
        requirement: Requirement,
    ) -> DeterministicRuleResult | None:
        """Run the optional deterministic rules for one requirement"""

        if self._rule_evaluator is None:
            return None
        return self._rule_evaluator.evaluate(requirement)

    def _assess_evidence(
        self,
        requirement: Requirement,
        evidence: Sequence[Evidence],
    ) -> EvidenceAssessment:
        """Assess evidence, retry one invalid selection, then fail safely."""

        # Skip a paid model call when retrieval found nothing
        if not evidence:
            return EvidenceAssessment(
                status=DecisionStatus.INSUFFICIENT_EVIDENCE,
                reason="No stored company evidence was retrieved",
            )

        assessment = self._assessment_client.assess(
            model=self._model,
            requirement=requirement,
            evidence=evidence,
        )
        invalid_ids = _invalid_evidence_ids(assessment, evidence)
        if not invalid_ids:
            return assessment

        logger.warning(
            "Retrying assessment for %s after invalid evidence IDs: %s",
            requirement.requirement_id,
            ", ".join(invalid_ids),
        )
        corrected_assessment = self._assessment_client.assess(
            model=self._model,
            requirement=requirement,
            evidence=evidence,
            rejected_evidence_ids=invalid_ids,
        )
        remaining_invalid_ids = _invalid_evidence_ids(corrected_assessment, evidence)
        if not remaining_invalid_ids:
            return corrected_assessment

        logger.warning(
            "Using human-review fallback for %s after invalid evidence IDs: %s",
            requirement.requirement_id,
            ", ".join(remaining_invalid_ids),
        )
        return EvidenceAssessment(
            status=DecisionStatus.REQUIRES_HUMAN_REVIEW,
            evidence_ids=_valid_evidence_ids(corrected_assessment, evidence),
            reason=INVALID_EVIDENCE_REVIEW_REASON,
        )

    def _load_evidence(
        self,
        requirement: Requirement,
        hits: Sequence[EvidenceSearchHit],
    ) -> tuple[list[Evidence], list[ToolCallTrace]]:
        """Load matched evidence records and record each lookup"""

        evidence: list[Evidence] = []
        tool_calls: list[ToolCallTrace] = []
        
        for hit in hits:
            item = self._evidence_reader.get_by_id(hit.evidence_id)
            evidence.append(item)
            tool_calls.append(
                ToolCallTrace(
                    requirement_id=requirement.requirement_id,
                    tool_name="get_evidence_by_id",
                    arguments={"evidence_id": item.evidence_id},
                    result_ids=[item.evidence_id],
                )
            )
        return evidence, tool_calls


def _build_assessment_input(
    requirement: Requirement,
    evidence: Sequence[Evidence],
    *,
    rejected_evidence_ids: Sequence[str] = (),
) -> str:
    payload = {
        "requirement": requirement.model_dump(mode="json"),
        "allowed_evidence_ids": [item.evidence_id for item in evidence],
        "candidate_evidence": [item.model_dump(mode="json") for item in evidence],
    }
    if rejected_evidence_ids:
        payload["validation_feedback"] = {
            "rejected_evidence_ids": list(rejected_evidence_ids),
            "instruction": (
                "Return only IDs copied exactly from allowed_evidence_ids, "
                "or return an empty evidence_ids list"
            ),
        }
    return json.dumps(payload, sort_keys=True)


def _unique_in_order(values: Sequence[str]) -> list[str]:
    """Remove duplicate values while keeping their first-seen order."""

    unique_values: list[str] = []
    seen_values: set[str] = set()
    for value in values:
        if value in seen_values:
            continue
        seen_values.add(value)
        unique_values.append(value)
    return unique_values


def _invalid_evidence_ids(
    assessment: EvidenceAssessment,
    evidence: Sequence[Evidence],
) -> list[str]:
    allowed_ids = {item.evidence_id for item in evidence}
    return [
        evidence_id
        for evidence_id in _unique_in_order(assessment.evidence_ids)
        if evidence_id not in allowed_ids
    ]


def _valid_evidence_ids(
    assessment: EvidenceAssessment,
    evidence: Sequence[Evidence],
) -> list[str]:
    allowed_ids = {item.evidence_id for item in evidence}
    return [
        evidence_id
        for evidence_id in _unique_in_order(assessment.evidence_ids)
        if evidence_id in allowed_ids
    ]


def _apply_status_policy(
    requirement: Requirement,
    assessment: EvidenceAssessment,
    rule_result: DeterministicRuleResult | None,
    selected_ids: Sequence[str],
) -> tuple[DecisionStatus, str]:
    """Choose the status after applying rule and review safeguards"""

    if rule_result is not None and rule_result.outcome is RuleOutcome.FAILED:
        # Model-proposed rules are reviewed before they can cause a definite rejection.
        return DecisionStatus.REQUIRES_HUMAN_REVIEW, rule_result.reason
    
    if (
        rule_result is not None
        and rule_result.outcome is RuleOutcome.REQUIRES_HUMAN_REVIEW
    ):
        return DecisionStatus.REQUIRES_HUMAN_REVIEW, rule_result.reason
    
    if requirement.requires_human_review:
        return (
            DecisionStatus.REQUIRES_HUMAN_REVIEW,
            "The extracted requirement requires human review",
        )
        
    if assessment.status is DecisionStatus.SATISFIED and not selected_ids:
        return (
            DecisionStatus.INSUFFICIENT_EVIDENCE,
            "A satisfied decision requires selected stored evidence",
        )
        
    return assessment.status, assessment.reason


def _enforce_decision_policy(
    requirement: Requirement,
    assessment: EvidenceAssessment,
    rule_result: DeterministicRuleResult | None,
) -> Decision:
    """Apply decision safeguards and build the final decision"""

    selected_ids = _unique_in_order(assessment.evidence_ids)

    status, reason = _apply_status_policy(
        requirement,
        assessment,
        rule_result,
        selected_ids,
    )

    return Decision(
        requirement_id=requirement.requirement_id,
        status=status,
        evidence_ids=selected_ids,
        reason=reason,
        rule_result=rule_result,
    )


def _should_report_progress(current: int, total: int) -> bool:
    """Emit about ten updates regardless of the number of requirements."""

    interval = max(1, (total + 9) // 10)
    return current % interval == 0 or current == total


def _recommend(
    requirements: Sequence[Requirement],
    decisions: Sequence[Decision],
) -> OverallRecommendation:
    """Choose bid, no-bid, or human review from mandatory decisions"""

    mandatory_requirement_ids = {
        requirement.requirement_id
        for requirement in requirements
        if requirement.requirement_type is RequirementType.MANDATORY
    }
    mandatory_decisions = [
        decision
        for decision in decisions
        if decision.requirement_id in mandatory_requirement_ids
    ]
    
    # A definite mandatory failure takes priority over unresolved ambiguity
    if any(
        decision.status is DecisionStatus.NOT_SATISFIED
        for decision in mandatory_decisions
    ):
        return OverallRecommendation.NO_BID
    # Other unresolved mandatory outcomes require human review
    if any(
        decision.status in MANDATORY_REVIEW_STATUSES
        for decision in mandatory_decisions
    ):
        return OverallRecommendation.HUMAN_REVIEW
    
    return OverallRecommendation.BID
