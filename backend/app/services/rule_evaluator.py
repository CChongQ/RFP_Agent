from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app.schemas import DeterministicRuleResult, Requirement, RuleOutcome
from app.schemas.rules import (
    AllowedValueCheck,
    CertificationValidityCheck,
    MinimumCountCheck,
    MinimumValueCheck,
    RuleSpec,
    ValidUntilCheck,
)
from app.services.deterministic_rules import (
    validate_allowed_value,
    validate_certification,
    validate_minimum,
    validate_valid_until,
)
from app.services.rule_evidence import RuleEvidenceService, RuleEvidenceValue

"""Evaluate extracted rules with exact evidence and deterministic checks.

Note:
- requirement: a tender requirement. Its rule:  the exact check
- Example: "at least 3 projects"= a minimum-count rule, with a minimum of 3.
"""


class DeterministicRuleEvaluator:
    """ Compares that evidence against the rule, and provide a deterministic result(pass, fail, or human review) """
    
    def __init__(self, evidence_service: RuleEvidenceService, *, as_of: date) -> None:
        self._evidence_service = evidence_service
        self._as_of = as_of

    def evaluate(self, requirement: Requirement) -> DeterministicRuleResult | None:
        """Evaluate all flat rules attached to one requirement"""

        if not requirement.rules:
            return None

        results = [self._evaluate_rule(rule) for rule in requirement.rules]
        return _combine_results(results)

    def _evaluate_rule(self, rule: RuleSpec) -> DeterministicRuleResult:
        
        #get evidences that needed for the rule
        evidence = self._evidence_service.read(rule)
        if evidence.problem is not None:
            return _human_review(rule, evidence.problem)

        check = rule.check
        if isinstance(check, MinimumCountCheck):
            return self._evaluate_minimum_count(rule, check, evidence)
        if isinstance(check, MinimumValueCheck):
            return self._evaluate_minimum_value(rule, check, evidence)
        if isinstance(check, AllowedValueCheck):
            return self._evaluate_allowed_value(rule, check, evidence)
        if isinstance(check, ValidUntilCheck):
            return validate_valid_until(
                evidence.valid_until,
                as_of=self._as_of,
                subject=rule.subject,
            )
        if isinstance(check, CertificationValidityCheck):
            return validate_certification(
                evidence.status,
                evidence.valid_until,
                as_of=self._as_of,
                subject=rule.subject,
            )
        raise TypeError(f"unsupported rule check: {type(check).__name__}")

    @staticmethod
    def _evaluate_minimum_count(
        rule: RuleSpec,
        check: MinimumCountCheck,
        evidence: RuleEvidenceValue,
    ) -> DeterministicRuleResult:
        
        value = evidence.value
        
        if value is None:
            actual = None
        elif isinstance(value, bool) or not isinstance(value, int):
            return _human_review(rule, "stored evidence count is not an int")
        else:
            actual = value
            
        return validate_minimum(
            actual,
            check.minimum,
            subject=rule.subject,
            rule_type=check.operator.value,
        )

    @staticmethod
    def _evaluate_minimum_value(
        rule: RuleSpec,
        check: MinimumValueCheck,
        evidence: RuleEvidenceValue,
    ) -> DeterministicRuleResult:
        
        value = evidence.value
        
        if value is None:
            actual = None
        elif isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
            return _human_review(rule, "stored evidence value is not numeric")
        else:
            actual = value
            
        return validate_minimum(
            actual,
            check.minimum,
            subject=rule.subject,
            rule_type=check.operator.value,
        )

    @staticmethod
    def _evaluate_allowed_value(
        rule: RuleSpec,
        check: AllowedValueCheck,
        evidence: RuleEvidenceValue,
    ) -> DeterministicRuleResult:
        
        value = evidence.value
        
        if value is None:
            actual = None
        elif not isinstance(value, str):
            return _human_review(rule, "stored evidence value is not text")
        else:
            actual = value
            
        return validate_allowed_value(
            actual,
            check.allowed_values,
            subject=rule.subject,
        )


def _human_review(rule: RuleSpec, reason: str) -> DeterministicRuleResult:
    """Return a review result for evidence that cannot be checked safely"""

    return DeterministicRuleResult(
        rule_type=rule.check.operator.value,
        outcome=RuleOutcome.REQUIRES_HUMAN_REVIEW,
        reason=f"{rule.subject}: {reason}",
    )


def _combine_results(
    results: Sequence[DeterministicRuleResult],
) -> DeterministicRuleResult:
    """Apply flat AND behavior without introducing a rule-expression language"""

    if len(results) == 1:
        return results[0]

    failed = next(
        (result for result in results if result.outcome is RuleOutcome.FAILED),
        None,
    )
    if failed is not None:
        return failed

    needs_review = next(
        (
            result
            for result in results
            if result.outcome is RuleOutcome.REQUIRES_HUMAN_REVIEW
        ),
        None,
    )
    if needs_review is not None:
        return needs_review

    return DeterministicRuleResult(
        rule_type="combined_rules",
        outcome=RuleOutcome.PASSED,
        reason=f"All {len(results)} deterministic checks passed",
    )
