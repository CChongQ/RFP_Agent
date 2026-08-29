from enum import StrEnum


class RequirementType(StrEnum):
    MANDATORY = "mandatory"
    SCORED = "scored"
    OPTIONAL = "optional"
    INFORMATIONAL = "informational"


class EvidenceType(StrEnum):
    COMPANY_PROFILE = "company_profile"
    PROJECT = "project"
    CERTIFICATION = "certification"
    CAPABILITY = "capability"
    POLICY = "policy"


class DecisionStatus(StrEnum):
    SATISFIED = "satisfied"
    PARTIALLY_SATISFIED = "partially_satisfied"
    NOT_SATISFIED = "not_satisfied"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"


class RuleOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
