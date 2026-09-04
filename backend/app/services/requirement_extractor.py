import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.prompts import REQUIREMENT_EXTRACTION_PROMPT
from app.schemas.extraction import (
    ExtractedEvidenceFilter,
    ExtractedRequirementCandidate,
    ExtractedRuleCandidate,
    ExtractedRuleParameterName,
    ExtractedScalarType,
    RequirementExtractionBatch,
)
from app.schemas.pdf import ExtractedBlock, ExtractedPage
from app.schemas.requirement import Requirement, SourceReference
from app.schemas.rules import (
    AllowedValueCheck,
    CertificationValidityCheck,
    EvidenceFilter,
    EvidenceSelector,
    MinimumCountCheck,
    MinimumValueCheck,
    RuleCandidate,
    RuleCheck,
    RuleOperator,
    RuleSpec,
    ValidUntilCheck,
)
from app.services.analysis_progress import AnalysisStage, ProgressReporter
from app.services.model_usage import ModelUsageTracker

"""Extract traceable requirements and deterministic rules from PDF pages using an LLM."""

PAGE_SEPARATOR = "\n\n"
logger = logging.getLogger(__name__)


class RequirementExtractionError(RuntimeError):
    """when structured requirements are missing or cannot be traced"""


class RuleCandidateConversionError(ValueError):
    """when a flat model rule cannot be converted into a domain rule"""


@dataclass(frozen=True)
class RequirementExtractionChunk:
    input_text: str
    source_block_ids: frozenset[str]


# ========== Requirement model client ==========


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
    """translator between this app and OpenAI Responses API"""

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
        
        # when model returns no parsed result    
        if response.output_parsed is None:
            incomplete_reason = (
                response.incomplete_details.reason
                if response.incomplete_details is not None
                else "unknown"
            )
            raise RequirementExtractionError(
                "model response did not contain structured requirements; "
                f"status={response.status}, reason={incomplete_reason}"
            )
        return response.output_parsed


# ========== Page formatting and chunking ==========


def _format_page(page: ExtractedPage) -> str:
    """For model input, format one PDF page and its blocks with block id"""

    formatted_blocks: list[str] = []
    for block in page.blocks:
        block_lines = [
            f'<source_block id="{block.block_id}">',
            block.text,
            "</source_block>",
        ]
        formatted_blocks.append("\n".join(block_lines))

    page_lines = [
        f'<pdf_page number="{page.page_number}">',
        "\n".join(formatted_blocks),
        "</pdf_page>",
    ]
    return "\n".join(page_lines)


def _build_page_chunks(
    pages: Sequence[ExtractedPage],
    max_chunk_characters: int,
) -> list[RequirementExtractionChunk]:
    """Combine consecutive PDF pages into chunks, without splitting a page.

    Example: Page 1 has 4k chars, Page 2 has 5k chars; with 10k limit -> Chunk 1 has 9k chars
    """

    if max_chunk_characters < 1:
        raise ValueError("max_chunk_characters must be at least 1")

    chunks: list[RequirementExtractionChunk] = []
    chunk_pages: list[str] = []
    chunk_source_ids: set[str] = set()
    chunk_length = 0

    for page in pages:
        page_block = _format_page(page)

        separator_length = len(PAGE_SEPARATOR) if chunk_pages else 0
        would_exceed_limit = (
            chunk_length + separator_length + len(page_block) > max_chunk_characters
        )

        if chunk_pages and would_exceed_limit:
            # exceed limit, start a new chunk first
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


# ========== Source block validation and tracing ==========


def _index_source_blocks(
    pages: Sequence[ExtractedPage],
) -> tuple[dict[str, ExtractedBlock], dict[str, tuple[int, int]]]:
    """Index each source block by ID and record its page and position in PDF"""

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
                raise RequirementExtractionError(f"source block ID {block.block_id} must be unique")

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

    selected_blocks: list[ExtractedBlock] = []
    for block_id in candidate.source_block_ids:
        if block_id not in blocks_by_id:
            raise RequirementExtractionError(
                f"requirement references unknown source block ID, example: {block_id}"
            )
        if block_id not in allowed_block_ids:
            raise RequirementExtractionError(
                f"requirement references source block outside the current model chunk: {block_id}"
            )
        selected_blocks.append(blocks_by_id[block_id])

    # Restore the blocks original reading order in the PDF
    selected_blocks.sort(key=lambda block: block_order[block.block_id])
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


# ========== Requirement and rule construction ==========



def _build_requirement(
    candidate: ExtractedRequirementCandidate,
    *,
    tender_id: str,
    requirement_number: int,
    source_page: int,
    source_excerpt: str,
    source_references: list[SourceReference],
) -> Requirement:
    # Convert a model-extracted requirement into the application's Requirement model.
    
    requirement_id = f"{tender_id}-REQ-{requirement_number:03d}"
    rules, rule_conversion_failed = _build_rule_specs(
        requirement_id,
        candidate.rule_candidates,
    )

    return Requirement(
        requirement_id=requirement_id,
        tender_id=tender_id,
        requirement_text=candidate.requirement_text,
        normalized_requirement=candidate.normalized_requirement,
        requirement_type=candidate.requirement_type,
        source_page=source_page,
        source_excerpt=source_excerpt,
        source_references=source_references,
        rules=rules,
        requires_human_review=(candidate.requires_human_review or rule_conversion_failed),
    )


def _build_rule_specs(
    requirement_id: str,
    candidates: Sequence[ExtractedRuleCandidate],
) -> tuple[list[RuleSpec], bool]:
    # Convert extracted rule candidates into RuleSpec objects and assign rule IDs.

    rules: list[RuleSpec] = []
    conversion_failed = False
    for candidate in candidates:
        try:
            domain_candidate = _convert_rule_candidate(candidate)
        except RuleCandidateConversionError as exc:
            conversion_failed = True
            logger.warning(
                "Ignoring invalid extracted rule for %s (%s): %s",
                requirement_id,
                candidate.operator.value,
                exc,
            )
            continue

        rules.append(
            RuleSpec(
                rule_id=f"{requirement_id}-RULE-{len(rules) + 1:03d}",
                subject=domain_candidate.subject,
                evidence_selector=domain_candidate.evidence_selector,
                check=domain_candidate.check,
            )
        )
    return rules, conversion_failed


def _convert_rule_candidate(candidate: ExtractedRuleCandidate) -> RuleCandidate:
    # Convert a flat extracted rule candidate into the typed domain model.

    try:
        parameters = _index_rule_parameters(candidate)
        check = _build_rule_check(candidate.operator, parameters)
        selector = EvidenceSelector(
            evidence_type=candidate.evidence_type,
            filters=[_convert_evidence_filter(item) for item in candidate.filters],
        )
        return RuleCandidate(
            subject=candidate.subject,
            evidence_selector=selector,
            check=check,
        )
    except (InvalidOperation, TypeError, ValueError, ValidationError) as exc:
        raise RuleCandidateConversionError(str(exc)) from exc


def _index_rule_parameters(
    candidate: ExtractedRuleCandidate,
) -> dict[ExtractedRuleParameterName, list[str]]:
    # Index a rule candidate's parameters by name and reject dup names
    
    parameters: dict[ExtractedRuleParameterName, list[str]] = {}
    for parameter in candidate.parameters:
        if parameter.name in parameters:
            raise ValueError(f"duplicate rule parameter: {parameter.name.value}")
        parameters[parameter.name] = parameter.values
    return parameters


def _build_rule_check(
    operator: RuleOperator,
    parameters: dict[ExtractedRuleParameterName, list[str]],
) -> RuleCheck:
    # Build the appropriate typed rule check for the extracted operator and parameters.

    if operator is RuleOperator.MINIMUM_COUNT:
        _require_parameter_names(parameters, {ExtractedRuleParameterName.MINIMUM})
        minimum = _single_parameter(
            parameters,
            ExtractedRuleParameterName.MINIMUM,
        )
        return MinimumCountCheck(operator=operator, minimum=int(minimum))

    if operator is RuleOperator.MINIMUM_VALUE:
        _require_parameter_names(
            parameters,
            {
                ExtractedRuleParameterName.VALUE_FIELD,
                ExtractedRuleParameterName.MINIMUM,
            },
        )
        return MinimumValueCheck(
            operator=operator,
            value_field=_single_parameter(
                parameters,
                ExtractedRuleParameterName.VALUE_FIELD,
            ),
            minimum=Decimal(_single_parameter(parameters, ExtractedRuleParameterName.MINIMUM)),
        )

    if operator is RuleOperator.ALLOWED_VALUE:
        _require_parameter_names(
            parameters,
            {
                ExtractedRuleParameterName.VALUE_FIELD,
                ExtractedRuleParameterName.ALLOWED_VALUES,
            },
        )
        return AllowedValueCheck(
            operator=operator,
            value_field=_single_parameter(
                parameters,
                ExtractedRuleParameterName.VALUE_FIELD,
            ),
            allowed_values=parameters[ExtractedRuleParameterName.ALLOWED_VALUES],
        )

    if operator is RuleOperator.VALID_UNTIL:
        _require_parameter_names(parameters, set())
        return ValidUntilCheck(operator=operator)

    if operator is RuleOperator.CERTIFICATION_VALIDITY:
        _require_parameter_names(parameters, set())
        return CertificationValidityCheck(operator=operator)

    raise ValueError(f"unsupported rule operator: {operator}")


def _require_parameter_names(
    parameters: dict[ExtractedRuleParameterName, list[str]],
    expected: set[ExtractedRuleParameterName],
) -> None:
    # Verify that a rule contains exactly the parameter names expected by its operator
    
    actual = set(parameters)
    if actual == expected:
        return
    expected_names = sorted(item.value for item in expected)
    actual_names = sorted(item.value for item in actual)
    raise ValueError(f"expected rule parameters {expected_names}, received {actual_names}")


def _single_parameter(
    parameters: dict[ExtractedRuleParameterName, list[str]],
    name: ExtractedRuleParameterName,
) -> str:
    # Return a scalar rule parameter after verifying that it has exactly one value.

    values = parameters[name]
    if len(values) != 1:
        raise ValueError(f"rule parameter {name.value} must contain exactly one value")
    return values[0]


def _convert_evidence_filter(candidate: ExtractedEvidenceFilter) -> EvidenceFilter:
    # Convert an extracted text-encoded filter value into its typed domain value.
    value_text = candidate.value_text
    if candidate.value_type is ExtractedScalarType.STRING:
        value: str | int | float | bool = value_text
    elif candidate.value_type is ExtractedScalarType.INTEGER:
        value = int(value_text)
    elif candidate.value_type is ExtractedScalarType.NUMBER:
        value = float(value_text)
        if not math.isfinite(value):
            raise ValueError("numeric evidence filter must be finite")
    elif candidate.value_type is ExtractedScalarType.BOOLEAN:
        normalized = value_text.casefold()
        if normalized not in {"true", "false"}:
            raise ValueError("boolean evidence filter must be true or false")
        value = normalized == "true"
    else:
        raise ValueError(f"unsupported evidence filter type: {candidate.value_type}")

    return EvidenceFilter(field=candidate.field, equals=value)


# ========== Requirement extraction orchestration ==========


def extract_requirements(
    pages: Sequence[ExtractedPage],
    *,
    tender_id: str,
    model: str,
    client: RequirementModelClient,
    max_chunk_characters: int = 12_000,
    progress_reporter: ProgressReporter | None = None,
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
    chunks = _build_page_chunks(pages, max_chunk_characters)
    
    if progress_reporter is not None:
        progress_reporter.stage_started(
            AnalysisStage.REQUIREMENT_EXTRACTION,
            total=len(chunks),
        )
        
    for chunk_number, chunk in enumerate(chunks, start=1):
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
            
            # Convert the model-extracted candidate into the this app's Requirement model
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

        if progress_reporter is not None:
            progress_reporter.stage_progress(
                AnalysisStage.REQUIREMENT_EXTRACTION,
                current=chunk_number,
                total=len(chunks),
            )

    if not requirements:
        raise RequirementExtractionError("model returned no requirements")

    if progress_reporter is not None:
        progress_reporter.stage_completed(
            AnalysisStage.REQUIREMENT_EXTRACTION,
            message=(
                "Requirement extraction completed: "
                f"{len(requirements)} requirements"
            ),
            details={"requirements_extracted": len(requirements)},
        )

    return requirements
