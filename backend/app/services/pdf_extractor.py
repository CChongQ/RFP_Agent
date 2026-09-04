from hashlib import sha256
from pathlib import Path
from typing import cast

import pymupdf

from app.schemas.pdf import (
    ExtractedBlock,
    ExtractedPage,
    PdfExtractionResult,
    PdfInspectionResult,
)


BYTES_PER_MEGABYTE = 1024 * 1024
HASH_READ_SIZE = BYTES_PER_MEGABYTE

type PdfTextBlock = tuple[float, float, float, float, str, int, int]


class PdfExtractionError(ValueError):
    """when a PDF cannot produce safe page-aware text"""


# ========== File validation and metadata ==========

def _calculate_sha256(path: Path) -> str:
    """Stream large files so hashing does not load the whole PDF into memory"""

    digest = sha256()
    with path.open("rb") as pdf_file:
        for chunk in iter(lambda: pdf_file.read(HASH_READ_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _validate_limits(
    *,
    max_pdf_mb: int,
    max_pdf_pages: int,
) -> None:
    if max_pdf_mb < 1:
        raise ValueError("max_pdf_mb must be at least 1")
    if max_pdf_pages < 1:
        raise ValueError("max_pdf_pages must be at least 1")


def _validate_pdf_file(path: Path, *, max_pdf_mb: int) -> int:
    """Validate PDF basic info"""

    if not path.is_file():
        raise PdfExtractionError(f"PDF file does not exist: {path}")
    if path.suffix.casefold() != ".pdf":
        raise PdfExtractionError(f"file must have a .pdf extension: {path.name}")
    
    #check size 
    file_size_bytes = path.stat().st_size
    if file_size_bytes == 0:
        raise PdfExtractionError(f"PDF file is empty: {path.name}")
    max_size_bytes = max_pdf_mb * BYTES_PER_MEGABYTE
    if file_size_bytes > max_size_bytes:
        raise PdfExtractionError(
            f"PDF is {file_size_bytes} bytes and exceeds the {max_pdf_mb} MB limit"
        )

    return file_size_bytes


def _open_pdf(path: Path) -> pymupdf.Document:
    try:
        return pymupdf.open(path)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise PdfExtractionError(f"unable to read PDF: {path.name}") from exc


def _validate_document(document: pymupdf.Document, *, max_pdf_pages: int) -> int:
    """Validate structural PDF facts"""

    if document.needs_pass:
        raise PdfExtractionError("password-protected PDFs are not supported")

    page_count = document.page_count
    if page_count < 1:
        raise PdfExtractionError("PDF contains no pages")
    if page_count > max_pdf_pages:
        raise PdfExtractionError(
            f"PDF has {page_count} pages, exceeds the {max_pdf_pages} page limit"
        )
    return page_count


# ========== Page text and block extraction ==========


def _read_page_content(
    document: pymupdf.Document,
    *,
    page_index: int,
    path: Path,
) -> tuple[str, list[PdfTextBlock]]:
    """Read one page's full text and sorted raw text blocks"""

    try:
        page = document.load_page(page_index)

        text = cast(str, page.get_text()).strip()
        raw_blocks = cast(list[PdfTextBlock], page.get_text("blocks", sort=True))

    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise PdfExtractionError(f"unable to read PDF: {path.name}") from exc
    return text, raw_blocks


def _extract_page_blocks(
    raw_blocks: list[PdfTextBlock],
    *,
    page_number: int,
) -> list[ExtractedBlock]:
    """Convert raw text blocks into traceable page blocks"""

    extracted_blocks: list[ExtractedBlock] = []

    for raw_block in raw_blocks:
        x0, y0, x1, y1, text, _source_block_number, block_type = raw_block

        normalized_text = text.strip()
        if block_type != 0 or not normalized_text:
            continue

        block_number = len(extracted_blocks) + 1
        extracted_blocks.append(
            ExtractedBlock(
                block_id=f"P{page_number:03d}-B{block_number:03d}", #each block's id
                page_number=page_number,
                text=normalized_text,
                bounding_box=(x0, y0, x1, y1),
            )
        )

    return extracted_blocks


def _extract_pages(
    document: pymupdf.Document,
    *,
    path: Path,
    page_count: int,
) -> list[ExtractedPage]:
    """Extract every validated PDF page into traceable blocks"""

    pages: list[ExtractedPage] = []
    for page_index in range(page_count):
        page_number = page_index + 1
        
        text, raw_blocks = _read_page_content(
            document,
            page_index=page_index,
            path=path,
        )
        blocks = _extract_page_blocks(raw_blocks, page_number=page_number)

        if text and not blocks:
            raise PdfExtractionError(
                f"PDF page {page_number} has text but no usable text blocks"
            )
        pages.append(
            ExtractedPage(
                page_number=page_number,
                text=text,
                blocks=blocks,
            )
        )

    return pages


def _count_text_characters(pages: list[ExtractedPage]) -> int:
    # Ignore whitespace so blank and image only PDF are rejected
    return sum(len("".join(page.text.split())) for page in pages)


# ========== PDF inspection and extraction ==========


def inspect_pdf(
    path: Path,
    *,
    max_pdf_mb: int = 25,
    max_pdf_pages: int = 250,
) -> PdfInspectionResult:
    """Read lightweight PDF facts without extracting page content"""

    _validate_limits(max_pdf_mb=max_pdf_mb, max_pdf_pages=max_pdf_pages)
    
    pdf_path = Path(path)
    file_size_bytes = _validate_pdf_file(pdf_path, max_pdf_mb=max_pdf_mb)

    with _open_pdf(pdf_path) as document:
        page_count = _validate_document(document, max_pdf_pages=max_pdf_pages)

    return PdfInspectionResult(
        source_path=pdf_path,
        document_sha256=_calculate_sha256(pdf_path),
        file_size_bytes=file_size_bytes,
        page_count=page_count,
    )


def extract_pdf(
    path: Path,
    *,
    max_pdf_mb: int = 25,
    max_pdf_pages: int = 250,
    min_text_characters: int = 20,
) -> PdfExtractionResult:
    """Validate a PDF and extract text"""

    _validate_limits(max_pdf_mb=max_pdf_mb, max_pdf_pages=max_pdf_pages)
    if min_text_characters < 0:
        raise ValueError("min_text_characters cannot be negative")

    pdf_path = Path(path)
    file_size_bytes = _validate_pdf_file(pdf_path, max_pdf_mb=max_pdf_mb)

    with _open_pdf(pdf_path) as document:
        page_count = _validate_document(document, max_pdf_pages=max_pdf_pages)
        pages = _extract_pages(
            document,
            path=pdf_path,
            page_count=page_count,
        )

    total_characters = _count_text_characters(pages)
    if total_characters < min_text_characters:
        raise PdfExtractionError(
            f"PDF contains only {total_characters} extractable text characters"
        )

    return PdfExtractionResult(
        source_path=pdf_path,
        document_sha256=_calculate_sha256(pdf_path),
        file_size_bytes=file_size_bytes,
        page_count=page_count,
        total_characters=total_characters,
        pages=pages,
    )
