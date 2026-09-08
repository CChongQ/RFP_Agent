from pathlib import Path
from typing import Annotated

from pydantic import Field

from app.schemas.base import NonEmptyString, SchemaModel, Sha256


class ExtractedBlock(SchemaModel):
    """Stores one deterministic text block from an extracted PDF page"""

    block_id: NonEmptyString
    page_number: Annotated[int, Field(ge=1)]
    text: NonEmptyString
    bounding_box: tuple[float, float, float, float]


class ExtractedPage(SchemaModel):
    """Stores text from one PDF page with a page number"""

    page_number: Annotated[int, Field(ge=1)]
    text: str
    blocks: list[ExtractedBlock]


class PdfInspectionResult(SchemaModel):
    """Describes lightweight structural facts about one validated PDF"""

    source_path: Path
    document_sha256: Sha256
    file_size_bytes: Annotated[int, Field(ge=1)]
    page_count: Annotated[int, Field(ge=1)]


class PdfExtractionResult(SchemaModel):
    """Describes the validated page-aware output from one PDF"""

    source_path: Path
    
    # Carry the source hash with extracted text to detect document substitution
    document_sha256: Sha256
    
    file_size_bytes: Annotated[int, Field(ge=1)]
    page_count: Annotated[int, Field(ge=1)]
    total_characters: Annotated[int, Field(ge=0)]
    pages: list[ExtractedPage] = Field(min_length=1)
