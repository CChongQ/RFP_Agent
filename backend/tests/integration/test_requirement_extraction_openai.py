import pytest
from openai import OpenAI

from app.core.config import get_settings
from app.services.pdf_extractor import extract_pdf
from app.services.requirement_extractor import (
    OpenAIRequirementModelClient,
    extract_requirements,
)
from app.services.tender_catalog import TenderCatalog

"""Test one real grounded requirement-extraction call against TENDER-001 page 1."""


EXPECTED_BLOCK_ID = "P001-B008"
EXPECTED_TEXT_FRAGMENT = "13MB to avoid"


@pytest.mark.external
def test_openai_extracts_requirement_with_real_source_block() -> None:
    settings = get_settings()

    if not settings.enable_external_api_calls:
        pytest.skip("External API calls are disabled")
    if settings.openai_api_key is None:
        pytest.skip("OPENAI_API_KEY is not configured")

    api_key = settings.openai_api_key.get_secret_value().strip()
    model = (settings.openai_model or "").strip()
    if not api_key:
        pytest.skip("OPENAI_API_KEY is empty")
    if not model:
        pytest.skip("OPENAI_MODEL is not configured")

    catalog = TenderCatalog(
        settings.tender_manifest_path,
        settings.tender_raw_dir,
    )

    tender, pdf_path = catalog.get("TENDER-001")
    print(f"Testing {pdf_path}....")

    extraction = extract_pdf(
        pdf_path,
        max_pdf_mb=settings.max_pdf_mb,
        max_pdf_pages=settings.max_pdf_pages,
    )
    page = extraction.pages[0]
    blocks_by_id = {block.block_id: block for block in page.blocks}

    # Check the local fixture before making the paid model request.
    expected_block = blocks_by_id.get(EXPECTED_BLOCK_ID)
    assert expected_block is not None
    assert EXPECTED_TEXT_FRAGMENT in expected_block.text

    openai_client = OpenAI(
        api_key=api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    requirements = extract_requirements(
        [page],
        tender_id=tender.tender_id,
        model=model,
        client=OpenAIRequirementModelClient(openai_client),
        max_chunk_characters=settings.max_requirement_chunk_characters,
    )

    assert requirements
    referenced_block_ids = {
        reference.block_id
        for requirement in requirements
        for reference in requirement.source_references
    }
    assert EXPECTED_BLOCK_ID in referenced_block_ids

    for requirement in requirements:
        reference_ids = [item.block_id for item in requirement.source_references]
        assert all(block_id in blocks_by_id for block_id in reference_ids)
        assert requirement.source_excerpt == "\n".join(
            blocks_by_id[block_id].text for block_id in reference_ids
        )
