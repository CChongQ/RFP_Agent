from hashlib import sha256
from pathlib import Path

import pymupdf

from app.schemas.pdf import ExtractedPage, PdfExtractionResult

BYTES_PER_MEGABYTE = 1024 * 1024
HASH_READ_SIZE = BYTES_PER_MEGABYTE


class PdfExtractionError(ValueError):
    """Raised when a PDF cannot produce safe page-aware text"""


def _calculate_sha256(path: Path) -> str:
    # Stream large files so hashing does not load the whole PDF into memory
    digest = sha256()
    with path.open("rb") as pdf_file:
        for chunk in iter(lambda: pdf_file.read(HASH_READ_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _validate_limits(
    *,
    max_pdf_mb: int,
    max_pdf_pages: int,
    min_text_characters: int,
) -> None:
    if max_pdf_mb < 1:
        raise ValueError("max_pdf_mb must be at least 1")
    if max_pdf_pages < 1:
        raise ValueError("max_pdf_pages must be at least 1")
    if min_text_characters < 0:
        raise ValueError("min_text_characters cannot be negative")


def _validate_pdf_file(path: Path, *, max_pdf_mb: int) -> int:
    if not path.is_file():
        raise PdfExtractionError(f"PDF file does not exist: {path}")
    if path.suffix.casefold() != ".pdf":
        raise PdfExtractionError(f"file must have a .pdf extension: {path.name}")

    file_size_bytes = path.stat().st_size
    max_size_bytes = max_pdf_mb * BYTES_PER_MEGABYTE
    if file_size_bytes > max_size_bytes:
        raise PdfExtractionError(
            f"PDF is {file_size_bytes} bytes and exceeds the {max_pdf_mb} MB limit"
        )
    return file_size_bytes


def _extract_pages(path: Path, *, max_pdf_pages: int) -> list[ExtractedPage]:
    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise PdfExtractionError("password-protected PDFs are not supported")
            if document.page_count < 1:
                raise PdfExtractionError("PDF contains no pages")
            if document.page_count > max_pdf_pages:
                raise PdfExtractionError(
                    f"PDF has {document.page_count} pages and exceeds "
                    f"the {max_pdf_pages}-page limit"
                )

            # Convert zero-based library indexes to page numbers shown to users.
            return [
                ExtractedPage(page_number=index + 1, text=page.get_text().strip())
                for index, page in enumerate(document)
            ]
    except PdfExtractionError:
        raise
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise PdfExtractionError(f"unable to read PDF: {path.name}") from exc


def _count_text_characters(pages: list[ExtractedPage]) -> int:
    # Ignore whitespace so blank and image-only PDFs are rejected.
    return sum(len("".join(page.text.split())) for page in pages)


def extract_pdf(
    path: Path,
    *,
    max_pdf_mb: int = 25,
    max_pdf_pages: int = 250,
    min_text_characters: int = 20,
) -> PdfExtractionResult:
    """Validate a PDF and extract text with 1-based page provenance"""

    _validate_limits(
        max_pdf_mb=max_pdf_mb,
        max_pdf_pages=max_pdf_pages,
        min_text_characters=min_text_characters,
    )
    pdf_path = Path(path)
    file_size_bytes = _validate_pdf_file(pdf_path, max_pdf_mb=max_pdf_mb)
    pages = _extract_pages(pdf_path, max_pdf_pages=max_pdf_pages)
    total_characters = _count_text_characters(pages)
    if total_characters < min_text_characters:
        raise PdfExtractionError(
            f"PDF contains only {total_characters} extractable text characters"
        )

    return PdfExtractionResult(
        source_path=pdf_path,
        document_sha256=_calculate_sha256(pdf_path),
        file_size_bytes=file_size_bytes,
        page_count=len(pages),
        total_characters=total_characters,
        pages=pages,
    )
