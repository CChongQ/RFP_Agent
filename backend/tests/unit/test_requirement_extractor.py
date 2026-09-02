"""Test turning model-selected PDF blocks into traceable requirements."""

import pytest

from app.schemas import (
    ExtractedBlock,
    ExtractedPage,
    ExtractedRequirementCandidate,
    RequirementExtractionBatch,
    RequirementType,
    RuleCandidate,
    RuleOperator,
)
from app.services.requirement_extractor import (
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
        assert model == "mock-model"
        assert "untrusted document data" in instructions
        assert "source_block_ids" in instructions
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
    blocks = [
        _block(page_number, position, text)
        for position, text in enumerate(texts, start=1)
    ]
    return ExtractedPage(
        page_number=page_number,
        text="\n".join(texts),
        blocks=blocks,
    )


def _candidate(
    *block_ids: str,
    rule_candidates: list[RuleCandidate] | None = None,
) -> ExtractedRequirementCandidate:
    return ExtractedRequirementCandidate(
        requirement_text="The bidder must provide implementation services",
        normalized_requirement="Provide implementation services",
        requirement_type=RequirementType.MANDATORY,
        source_block_ids=list(block_ids),
        rule_candidates=rule_candidates or [],
    )


def _minimum_count_candidate() -> RuleCandidate:
    """Build one fictional model-proposed count rule."""

    return RuleCandidate.model_validate(
        {
            "subject": "qualifying projects",
            "evidence_selector": {"evidence_type": "project"},
            "check": {"operator": "minimum_count", "minimum": 3},
        }
    )


def test_extract_requirements_resolves_exact_source_block() -> None:
    source_text = (
        "Note to Bidders: ensure e-mails do not exceed 13MB to avoid\n"
        "problems with transmission."
    )
    pages = [_page(1, "Introduction", source_text, "Submission details")]
    client = FakeRequirementModelClient(
        [
            RequirementExtractionBatch(
                requirements=[_candidate("P001-B002")]
            )
        ]
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
    assert requirement.rules == []
    assert '<source_block id="P001-B002">' in client.calls[0]


def test_extract_requirements_assigns_rule_ids_in_application_code() -> None:
    pages = [_page(1, "The bidder must provide at least three qualifying projects")]
   
    candidate = _candidate(
        "P001-B001",
        rule_candidates=[_minimum_count_candidate()],
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

    assert requirement.rules[0].rule_id == "TENDER-TEST-001-REQ-001-RULE-001"
    assert requirement.rules[0].check.operator is RuleOperator.MINIMUM_COUNT


def test_extract_requirements_orders_and_joins_multiple_source_blocks() -> None:
    pages = [
        _page(
            1,
            "The bidder must provide implementation services.",
            "The services must begin within thirty days.",
        )
    ]
    client = FakeRequirementModelClient(
        [
            RequirementExtractionBatch(
                requirements=[_candidate("P001-B002", "P001-B001")]
            )
        ]
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


def test_extract_requirements_chunks_whole_pages() -> None:
    pages = [
        _page(1, "The bidder must provide migration services"),
        _page(2, "Experience will be evaluated and scored"),
    ]
    client = FakeRequirementModelClient(
        [
            RequirementExtractionBatch(
                requirements=[_candidate("P001-B001")]
            ),
            RequirementExtractionBatch(
                requirements=[_candidate("P002-B001")]
            ),
        ]
    )

    requirements = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
        max_chunk_characters=150,
    )

    assert len(client.calls) == 2
    assert [requirement.source_page for requirement in requirements] == [1, 2]


def test_extract_requirements_rejects_unknown_source_block() -> None:
    pages = [_page(1, "Known tender text")]
    client = FakeRequirementModelClient(
        [
            RequirementExtractionBatch(
                requirements=[_candidate("P001-B999")]
            )
        ]
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
        [
            RequirementExtractionBatch(
                requirements=[_candidate("P002-B001")]
            )
        ]
    )

    with pytest.raises(RequirementExtractionError, match="outside the current model chunk"):
        extract_requirements(
            pages,
            tender_id="TENDER-TEST-001",
            model="mock-model",
            client=client,
            max_chunk_characters=150,
        )
