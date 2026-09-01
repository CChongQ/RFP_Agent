from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from app.prompts import REQUIREMENT_EXTRACTION_PROMPT
from app.schemas.extraction import ExtractedRequirementCandidate, RequirementExtractionBatch
from app.schemas.pdf import ExtractedBlock, ExtractedPage
from app.schemas.requirement import Requirement, SourceReference
from app.services.model_usage import ModelUsageTracker

PAGE_SEPARATOR = "\n\n"


class RequirementExtractionError(RuntimeError):
    """when structured requirements are missing or cannot be traced"""


@dataclass(frozen=True)
class RequirementExtractionChunk:
    """Keeps model input paired with the source blocks it may reference"""

    input_text: str
    source_block_ids: frozenset[str]


class RequirementModelClient(Protocol):
    """Defines the model boundary used by the extraction service"""

    def parse_requirements(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
    ) -> RequirementExtractionBatch: ...


class OpenAIRequirementModelClient:
    """Adapts the OpenAI Responses API to the local extraction boundary"""

    def __init__(
        self,
        client: OpenAI,
        usage_tracker: ModelUsageTracker | None = None,
    ) -> None:
        self._client = client
        self._usage_tracker = usage_tracker

    def parse_requirements(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
    ) -> RequirementExtractionBatch:
        
        response = self._client.responses.parse(
            model=model,
            instructions=instructions,
            input=input_text,
            text_format=RequirementExtractionBatch,
            store=False,
        )
        
        # record usage
        if self._usage_tracker is not None and response.usage is not None:
            self._usage_tracker.add(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
        if response.output_parsed is None:
            raise RequirementExtractionError(
                "model response did not contain structured requirements"
            )
        return response.output_parsed


def _format_page(page: ExtractedPage) -> str:
    # Generated block IDs let the model cite source text without copying it
    formatted_blocks = "\n".join(
        (
            f'<source_block id="{block.block_id}">\n'
            f"{block.text}\n"
            "</source_block>"
        )
        for block in page.blocks
    )
    return f'<pdf_page number="{page.page_number}">\n{formatted_blocks}\n</pdf_page>'


def _build_page_chunks(
    pages: Sequence[ExtractedPage],
    max_chunk_characters: int,
) -> list[RequirementExtractionChunk]:
    
    if max_chunk_characters < 1:
        raise ValueError("max_chunk_characters must be at least 1")

    chunks: list[RequirementExtractionChunk] = []
    chunk_pages: list[str] = []
    chunk_source_ids: set[str] = set()
    chunk_length = 0

    # Keep pages intact so a chunk boundary never breaks page provenance
    for page in pages:
        
        page_block = _format_page(page)
        
        separator_length = len(PAGE_SEPARATOR) if chunk_pages else 0
        would_exceed_limit = (
            chunk_length + separator_length + len(page_block) > max_chunk_characters
        )
        
        if chunk_pages and would_exceed_limit:
            chunks.append(
                RequirementExtractionChunk(
                    input_text=PAGE_SEPARATOR.join(chunk_pages),
                    source_block_ids=frozenset(chunk_source_ids),
                )
            )
            chunk_pages = []
            chunk_source_ids = set()
            chunk_length = 0
            separator_length = 0

        chunk_pages.append(page_block)
        chunk_source_ids.update(block.block_id for block in page.blocks)
        chunk_length += separator_length + len(page_block)

    if chunk_pages:
        chunks.append(
            RequirementExtractionChunk(
                input_text=PAGE_SEPARATOR.join(chunk_pages),
                source_block_ids=frozenset(chunk_source_ids),
            )
        )
    return chunks


def _index_source_blocks(
    pages: Sequence[ExtractedPage],
) -> tuple[dict[str, ExtractedBlock], dict[str, tuple[int, int]]]:
    
    page_numbers = [page.page_number for page in pages]
    if len(set(page_numbers)) != len(page_numbers):
        raise RequirementExtractionError("page numbers must be unique")
    
    blocks_by_id: dict[str, ExtractedBlock] = {}
    block_order: dict[str, tuple[int, int]] = {}
    for page in pages:
        for position, block in enumerate(page.blocks):
            if block.page_number != page.page_number:
                raise RequirementExtractionError(
                    f"source block {block.block_id} has an inconsistent page number"
                )
            if block.block_id in blocks_by_id:
                raise RequirementExtractionError(
                    f"source block ID {block.block_id} must be unique"
                )
            blocks_by_id[block.block_id] = block
            block_order[block.block_id] = (page.page_number, position)
    return blocks_by_id, block_order


def _resolve_candidate_source(
    candidate: ExtractedRequirementCandidate,
    *,
    blocks_by_id: dict[str, ExtractedBlock],
    block_order: dict[str, tuple[int, int]],
    allowed_block_ids: frozenset[str],
) -> tuple[int, str, list[SourceReference]]:
    """Validate model-selected IDs and rebuild source data from PDF blocks"""

    if len(candidate.source_block_ids) != len(set(candidate.source_block_ids)):
        raise RequirementExtractionError("requirement source block IDs must be unique")

    unknown_ids = [
        block_id
        for block_id in candidate.source_block_ids
        if block_id not in blocks_by_id
    ]
    if unknown_ids:
        raise RequirementExtractionError(
            f"requirement references unknown source block ID {unknown_ids[0]}"
        )

    outside_chunk_ids = [
        block_id
        for block_id in candidate.source_block_ids
        if block_id not in allowed_block_ids
    ]
    if outside_chunk_ids:
        raise RequirementExtractionError(
            "requirement references source block outside the current model chunk: "
            f"{outside_chunk_ids[0]}"
        )

    selected_blocks = sorted(
        (blocks_by_id[block_id] for block_id in candidate.source_block_ids),
        key=lambda block: block_order[block.block_id],
    )
    source_references = [
        SourceReference(
            block_id=block.block_id,
            page_number=block.page_number,
            bounding_box=block.bounding_box,
        )
        for block in selected_blocks
    ]
    
    source_excerpt = "\n".join(block.text for block in selected_blocks)
    
    return selected_blocks[0].page_number, source_excerpt, source_references


def _build_requirement(
    candidate: ExtractedRequirementCandidate,
    *,
    tender_id: str,
    requirement_number: int,
    source_page: int,
    source_excerpt: str,
    source_references: list[SourceReference],
) -> Requirement:
    
    # safety check: model output cannot change the ID format
    return Requirement(
        requirement_id=f"{tender_id}-REQ-{requirement_number:03d}",
        tender_id=tender_id,
        requirement_text=candidate.requirement_text,
        normalized_requirement=candidate.normalized_requirement,
        requirement_type=candidate.requirement_type,
        source_page=source_page,
        source_excerpt=source_excerpt,
        source_references=source_references,
        requires_human_review=candidate.requires_human_review,
    )


def extract_requirements(
    pages: Sequence[ExtractedPage],
    *,
    tender_id: str,
    model: str,
    client: RequirementModelClient,
    max_chunk_characters: int = 12_000,
) -> list[Requirement]:
    """Extract and validate traceable requirements"""

    if not pages:
        raise RequirementExtractionError("at least 1 extracted page is required")
    if not tender_id.strip():
        raise ValueError("tender_id cannot be empty")
    if not model.strip():
        raise ValueError("model cannot be empty")

    blocks_by_id, block_order = _index_source_blocks(pages)

    requirements: list[Requirement] = []
    for chunk in _build_page_chunks(pages, max_chunk_characters):
        
        batch = client.parse_requirements(
            model=model,
            instructions=REQUIREMENT_EXTRACTION_PROMPT.instructions,
            input_text=chunk.input_text,
        )
        
        for candidate in batch.requirements:
            source_page, source_excerpt, source_references = _resolve_candidate_source(
                candidate,
                blocks_by_id=blocks_by_id,
                block_order=block_order,
                allowed_block_ids=chunk.source_block_ids,
            )
            requirements.append(
                _build_requirement(
                    candidate,
                    tender_id=tender_id,
                    requirement_number=len(requirements) + 1,
                    source_page=source_page,
                    source_excerpt=source_excerpt,
                    source_references=source_references,
                )
            )

    if not requirements:
        raise RequirementExtractionError("model returned no requirements")
    return requirements
