from pathlib import Path

import pymupdf
import pytest

from app.services.pdf_extractor import PdfExtractionError, extract_pdf

"""
Test PDF validation, text extraction, and file metadata
"""


# Generate tiny PDFs for testing only
def _write_pdf(path: Path, page_texts: list[str]) -> None:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


# Basic tests

def test_extract_pdf_returns_one_based_pages_and_hash(tmp_path: Path) -> None:
    pdf_path = tmp_path / "example.pdf"
    _write_pdf(
        pdf_path,
        [
            "The bidder must provide implementation services",
            "Experience will be evaluated and scored",
        ],
    )

    result = extract_pdf(pdf_path)

    assert result.page_count == 2
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "implementation services" in result.pages[0].text
    assert result.pages[0].blocks[0].block_id == "P001-B001"
    assert result.pages[0].blocks[0].page_number == 1
    assert "implementation services" in result.pages[0].blocks[0].text
    assert len(result.pages[0].blocks[0].bounding_box) == 4
    assert len(result.document_sha256) == 64


# Corner-case tests

def test_extract_pdf_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PdfExtractionError, match="does not exist"):
        extract_pdf(tmp_path / "missing.pdf")


def test_extract_pdf_rejects_non_pdf_extension(tmp_path: Path) -> None:
    text_path = tmp_path / "example.txt"
    text_path.write_text("This is not a PDF", encoding="utf-8")

    with pytest.raises(PdfExtractionError, match=r"\.pdf extension"):
        extract_pdf(text_path)


def test_extract_pdf_rejects_file_size_limit(tmp_path: Path) -> None:
    pdf_path = tmp_path / "oversized.pdf"
    pdf_path.write_bytes(b"0" * (1024 * 1024 + 1))

    with pytest.raises(PdfExtractionError, match="exceeds the 1 MB limit"):
        extract_pdf(pdf_path, max_pdf_mb=1)


def test_extract_pdf_rejects_corrupt_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"This is not valid PDF data")

    with pytest.raises(PdfExtractionError, match="unable to read PDF"):
        extract_pdf(pdf_path)


def test_extract_pdf_rejects_page_limit(tmp_path: Path) -> None:
    pdf_path = tmp_path / "two-pages.pdf"
    _write_pdf(pdf_path, ["First page contains enough text", "Second page contains enough text"])

    with pytest.raises(PdfExtractionError, match="exceeds the 1-page limit"):
        extract_pdf(pdf_path, max_pdf_pages=1)


def test_extract_pdf_rejects_document_without_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "empty.pdf"
    _write_pdf(pdf_path, [""])

    with pytest.raises(PdfExtractionError, match="extractable text characters"):
        extract_pdf(pdf_path)
