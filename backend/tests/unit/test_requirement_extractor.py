import pytest

from app.schemas import (
    ExtractedPage,
    ExtractedRequirementCandidate,
    RequirementExtractionBatch,
    RequirementType,
)
from app.services.requirement_extractor import (
    RequirementExtractionError,
    extract_requirements,
)
"""
Test turning model output into source-linked requirements.

"""


class FakeRequirementModelClient:
    """Returns synthetic structured responses"""

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
        self.calls.append(input_text)
        return next(self._batches)


def _candidate(*, source_page: int, excerpt: str) -> ExtractedRequirementCandidate:
    return ExtractedRequirementCandidate(
        requirement_text=excerpt,
        normalized_requirement="Provide implementation services",
        requirement_type=RequirementType.MANDATORY,
        source_page=source_page,
        source_excerpt=excerpt,
    )


# Basic tests

def test_extract_requirements_returns_traceable_models() -> None:
    excerpt = "The bidder must provide implementation services"
    pages = [ExtractedPage(page_number=1, text=f"Introduction\n{excerpt}\nSubmission details")]
    
    client = FakeRequirementModelClient(
        [RequirementExtractionBatch(requirements=[_candidate(source_page=1, excerpt=excerpt)])]
    )

    requirements = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
    )

    assert requirements[0].requirement_id == "TENDER-TEST-001-REQ-001"
    assert requirements[0].source_page == 1
    assert requirements[0].source_excerpt == excerpt
    assert len(client.calls) == 1


def test_extract_requirements_chunks_whole_pages() -> None:
    first_excerpt = "The bidder must provide migration services"
    second_excerpt = "Experience will be evaluated and scored"
    pages = [
        ExtractedPage(page_number=1, text=first_excerpt),
        ExtractedPage(page_number=2, text=second_excerpt),
    ]
    
    client = FakeRequirementModelClient(
        [
            RequirementExtractionBatch(
                requirements=[_candidate(source_page=1, excerpt=first_excerpt)]
            ),
            RequirementExtractionBatch(
                requirements=[_candidate(source_page=2, excerpt=second_excerpt)]
            ),
        ]
    )

    requirements = extract_requirements(
        pages,
        tender_id="TENDER-TEST-001",
        model="mock-model",
        client=client,
        max_chunk_characters=60,
    )

    assert len(client.calls) == 2
    assert [requirement.source_page for requirement in requirements] == [1, 2]


# Corner-case tests

def test_extract_requirements_rejects_unknown_source_page() -> None:
    pages = [ExtractedPage(page_number=1, text="Known tender text")]
    
    client = FakeRequirementModelClient(
        [
            RequirementExtractionBatch(
                requirements=[_candidate(source_page=2, excerpt="Invented requirement")]
            )
        ]
    )

    with pytest.raises(RequirementExtractionError, match="unavailable PDF page 2"):
        extract_requirements(
            pages,
            tender_id="TENDER-TEST-001",
            model="mock-model",
            client=client,
        )


def test_extract_requirements_rejects_untraceable_excerpt() -> None:
    pages = [ExtractedPage(page_number=1, text="Known tender text")]
    
    client = FakeRequirementModelClient(
        [
            RequirementExtractionBatch(
                requirements=[_candidate(source_page=1, excerpt="Invented requirement")]
            )
        ]
    )

    with pytest.raises(RequirementExtractionError, match="not present on PDF page 1"):
        extract_requirements(
            pages,
            tender_id="TENDER-TEST-001",
            model="mock-model",
            client=client,
        )
