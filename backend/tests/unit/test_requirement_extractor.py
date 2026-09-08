"""Test turning model-selected PDF blocks into traceable requirements."""

from unittest.mock import Mock

import pytest

from app.schemas import (
    EvidenceType,
    ExtractedBlock,
    ExtractedEvidenceFilter,
    ExtractedPage,
    ExtractedRequirementCandidate,
    ExtractedRuleCandidate,
    ExtractedRuleParameter,
    ExtractedRuleParameterName,
    ExtractedScalarType,
    RequirementExtractionBatch,
    RequirementType,
    RuleOperator,
)
from app.services.model_usage import ModelUsageTracker
from app.services.requirement_extractor import (
    OpenAIRequirementModelClient,
    RequirementExtractionError,
    extract_requirements,
)


class FakeRequirementModelClient:
    """Returns synthetic structured responses."""

    def __init__(self, batches: list[RequirementExtractionBatch]) -> None:
        self._batches = iter(batches)
        self.calls: list[str] = []

    def parse_requirements(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
    ) -> RequirementExtractionBatch:
        self.calls.append(input_text)
        return next(self._batches)


def _block(page_number: int, position: int, text: str) -> ExtractedBlock:
    return ExtractedBlock(
        block_id=f"P{page_number:03d}-B{position:03d}",
        page_number=page_number,
        text=text,
        bounding_box=(72.0, 72.0, 500.0, 100.0),
    )


def _page(page_number: int, *texts: str) -> ExtractedPage:
    blocks = [_block(page_number, position, text) for position, text in enumerate(texts, start=1)]
    return ExtractedPage(
        page_number=page_number,
        text="\n".join(texts),
        blocks=blocks,
    )


def _candidate(
    *block_ids: str,
    rule_candidates: list[ExtractedRuleCandidate] | None = None,
) -> ExtractedRequirementCandidate:
    return ExtractedRequirementCandidate(
        requirement_text="The bidder must provide implementation services",
        normalized_requirement="Provide implementation services",
        requirement_type=RequirementType.MANDATORY,
        source_block_ids=list(block_ids),
        rule_candidates=rule_candidates or [],
        requires_human_review=False,
    )


def _minimum_count_candidate(
    *,
    minimum: str = "3",
    filters: list[ExtractedEvidenceFilter] | None = None,
) -> ExtractedRuleCandidate:
    """Build one fictional model-proposed count rule."""

    return ExtractedRuleCandidate(
        subject="qualifying projects",
        evidence_type=EvidenceType.PROJECT,
        filters=filters or [],
        operator=RuleOperator.MINIMUM_COUNT,
        parameters=[
            ExtractedRuleParameter(
                name=ExtractedRuleParameterName.MINIMUM,
                values=[minimum],
            )
        ],
    )


def _rule_candidate(
    operator: RuleOperator,
    parameters: list[tuple[ExtractedRuleParameterName, list[str]]],
    *,
    evidence_type: EvidenceType = EvidenceType.PROJECT,
    filters: list[ExtractedEvidenceFilter] | None = None,
) -> ExtractedRuleCandidate:
    
    return ExtractedRuleCandidate(
        subject="qualifying evidence",
        evidence_type=evidence_type,
        filters=filters or [],
        operator=operator,
        parameters=[
            ExtractedRuleParameter(name=name, values=values) for name, values in parameters
        ],
    )


@pytest.mark.parametrize("reports_usage", [True, False])
def test_requirement_client_records_available_response_usage(reports_usage: bool) -> None:
    tracker = ModelUsageTracker()
    batch = RequirementExtractionBatch(requirements=[])
    client = Mock()
    client.responses.parse.return_value = Mock(
        output_parsed=batch,
        usage=Mock(input_tokens=100, output_tokens=40) if reports_usage else None,
    )

    result = OpenAIRequirementModelClient(client, tracker).parse_requirements(
        model="mock-model", instructions="Extract requirements", input_text="Tender text"
    )

    assert result == batch
    usage = tracker.snapshot()
    assert usage.input_tokens == (100 if reports_usage else 0)
    assert usage.output_tokens == (40 if reports_usage else 0)
    assert usage.unreported_calls == (0 if reports_usage else 1)


def test_extract_requirements_resolves_exact_source_block() -> None:
    source_text = "The bidder must provide\nimplementation services"
    pages = [_page(1, "Introduction", source_text, "Submission details")]
    client = FakeRequirementModelClient(
        [RequirementExtractionBatch(requirements=[_candidate("P001-B002")])]
    )

    requirements = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
    )

    requirement = requirements[0]
    assert requirement.requirement_id == "TENDER-TEST-001-REQ-001"
    assert requirement.source_page == 1
    assert requirement.source_excerpt == source_text
    assert requirement.source_references[0].block_id == "P001-B002"
    assert requirement.requirement_text == "The bidder must provide implementation services"
    assert requirement.rules == []
    assert '<source_block id="P001-B002">' in client.calls[0]


@pytest.mark.parametrize(
    ("rule_candidate", "expected_check"),
    [
        (
            _rule_candidate(
                RuleOperator.MINIMUM_COUNT,
                [(ExtractedRuleParameterName.MINIMUM, ["3"])],
            ),
            {"operator": "minimum_count", "minimum": 3},
        ),
        (
            _rule_candidate(
                RuleOperator.MINIMUM_VALUE,
                [
                    (ExtractedRuleParameterName.VALUE_FIELD, ["contract_value"]),
                    (ExtractedRuleParameterName.MINIMUM, ["100000.50"]),
                ],
            ),
            {
                "operator": "minimum_value",
                "value_field": "contract_value",
                "minimum": "100000.50",
            },
        ),
        (
            _rule_candidate(
                RuleOperator.ALLOWED_VALUE,
                [
                    (ExtractedRuleParameterName.VALUE_FIELD, ["industry"]),
                    (
                        ExtractedRuleParameterName.ALLOWED_VALUES,
                        ["education", "healthcare"],
                    ),
                ],
            ),
            {
                "operator": "allowed_value",
                "value_field": "industry",
                "allowed_values": ["education", "healthcare"],
            },
        ),
        (
            _rule_candidate(RuleOperator.VALID_UNTIL, []),
            {"operator": "valid_until"},
        ),
        (
            _rule_candidate(
                RuleOperator.CERTIFICATION_VALIDITY,
                [],
                evidence_type=EvidenceType.CERTIFICATION,
                filters=[
                    ExtractedEvidenceFilter(
                        field="name",
                        value_type=ExtractedScalarType.STRING,
                        value_text="Example Certification",
                    )
                ],
            ),
            {"operator": "certification_validity"},
        ),
    ],
)
def test_extract_requirements_converts_supported_rule_operators(
    rule_candidate: ExtractedRuleCandidate,
    expected_check: dict[str, object],
) -> None:
    pages = [_page(1, "The bidder must satisfy a measurable requirement")]
    
    candidate = _candidate("P001-B001", rule_candidates=[rule_candidate])
    client = FakeRequirementModelClient([RequirementExtractionBatch(requirements=[candidate])])

    requirement = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
    )[0]

    assert requirement.rules[0].check.model_dump(mode="json") == expected_check
    assert requirement.rules[0].rule_id == "TENDER-TEST-001-REQ-001-RULE-001"
    assert requirement.requires_human_review is False


@pytest.mark.parametrize("value_text,expected", [("true", True), ("false", False)])
def test_extract_requirements_converts_boolean_filter_values(
    value_text: str,
    expected: bool,
) -> None:
    # Conversion only here: query-field approval belongs to RuleEvidenceService tests
    pages = [_page(1, "The bidder must provide three projects with a specified location status")]
    
    candidate = _candidate(
        "P001-B001",
        rule_candidates=[
            _minimum_count_candidate(
                filters=[
                    ExtractedEvidenceFilter(
                        field="is_canadian",
                        value_type=ExtractedScalarType.BOOLEAN,
                        value_text=value_text,
                    )
                ]
            )
        ],
    )
    client = FakeRequirementModelClient([RequirementExtractionBatch(requirements=[candidate])])

    requirement = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
    )[0]

    assert requirement.rules[0].evidence_selector.filters[0].equals is expected


@pytest.mark.parametrize(
    "rule_candidate",
    [
        _minimum_count_candidate(minimum="three"),
        _rule_candidate(
            RuleOperator.MINIMUM_VALUE,
            [
                (ExtractedRuleParameterName.VALUE_FIELD, ["contract_value"]),
                (ExtractedRuleParameterName.MINIMUM, ["not-a-number"]),
            ],
        ),
    ],
)
@pytest.mark.parametrize("include_valid_rule", [False, True])
def test_invalid_extracted_rule_keeps_requirement_for_human_review(
    rule_candidate: ExtractedRuleCandidate,
    include_valid_rule: bool,
) -> None:
    pages = [_page(1, "The bidder must meet a measurable requirement")]
    
    rules = [rule_candidate]
    
    if include_valid_rule:
        rules.append(_minimum_count_candidate(minimum="5"))
    candidate = _candidate(
        "P001-B001",
        rule_candidates=rules,
    )
    client = FakeRequirementModelClient(
        [RequirementExtractionBatch(requirements=[candidate])]
    )

    requirement = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
    )[0]

    if include_valid_rule:
        assert len(requirement.rules) == 1
        assert requirement.rules[0].check.model_dump(mode="json") == {
            "operator": "minimum_count",
            "minimum": 5,
        }
        assert requirement.rules[0].rule_id == "TENDER-TEST-001-REQ-001-RULE-001"
    else:
        assert requirement.rules == []
        
    assert requirement.source_excerpt == pages[0].text
    assert requirement.requires_human_review is True


def test_extract_requirements_orders_and_joins_multiple_source_blocks() -> None:
    pages = [
        _page(
            1,
            "The bidder must provide implementation services.",
            "The services must begin within thirty days.",
        )
    ]
    client = FakeRequirementModelClient(
        [RequirementExtractionBatch(requirements=[_candidate("P001-B002", "P001-B001")])]
    )

    requirement = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
    )[0]

    assert requirement.source_excerpt == (
        "The bidder must provide implementation services.\n"
        "The services must begin within thirty days."
    )
    assert [item.block_id for item in requirement.source_references] == [
        "P001-B001",
        "P001-B002",
    ]


def test_extract_requirements_keeps_pages_intact_and_continues_after_empty_chunk() -> None:
    pages = [
        _page(1, "Introduction and tender background"),
        _page(2, "The bidder must provide", "implementation services"),
        _page(3, "The bidder must provide implementation services"),
    ]
    client = FakeRequirementModelClient(
        [
            RequirementExtractionBatch(requirements=[]),
            RequirementExtractionBatch(requirements=[_candidate("P002-B001", "P002-B002")]),
            RequirementExtractionBatch(requirements=[_candidate("P003-B001")]),
        ]
    )

    requirements = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
        max_chunk_characters=150,
    )

    assert len(client.calls) == 3
    # A page stays intact even when it alone exceeds the chunk limit.
    assert "P002-B001" in client.calls[1]
    assert "P002-B002" in client.calls[1]
    assert "P003-B001" not in client.calls[1]
    assert [requirement.source_page for requirement in requirements] == [2, 3]
    # An introductory chunk with no requirements must not stop extraction or consume an ID.
    assert [requirement.requirement_id for requirement in requirements] == [
        "TENDER-TEST-001-REQ-001",
        "TENDER-TEST-001-REQ-002",
    ]


def test_extract_requirements_rejects_unknown_source_block() -> None:
    pages = [_page(1, "Known tender text")]
    client = FakeRequirementModelClient(
        [RequirementExtractionBatch(requirements=[_candidate("P001-B999")])]
    )

    with pytest.raises(RequirementExtractionError, match="unknown source block ID"):
        extract_requirements(
            pages,
            tender_id="TENDER-TEST-001",
            model="mock-model",
            client=client,
        )


def test_extract_requirements_rejects_source_block_outside_chunk() -> None:
    pages = [
        _page(1, "The bidder must provide migration services"),
        _page(2, "Experience will be evaluated and scored"),
    ]
    client = FakeRequirementModelClient(
        [RequirementExtractionBatch(requirements=[_candidate("P002-B001")])]
    )

    with pytest.raises(RequirementExtractionError, match="outside the current model chunk"):
        extract_requirements(
            pages,
            tender_id="TENDER-TEST-001",
            model="mock-model",
            client=client,
            max_chunk_characters=150,
        )
