"""Check PDF flow: generate one page, reopen it, and recover its text with PyMuPDF."""

from pathlib import Path

import pymupdf


def test_pymupdf_extracts_page_text(tmp_path: Path) -> None:
    # generate a temp PDF containing a known tender requirement.
    pdf_path = tmp_path / "smoke-test.pdf"

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Supplier must provide evidence of cloud migration experience.",
    )
    document.save(pdf_path)
    document.close()

    # parse the saved file and verify its page count and extracted text.
    with pymupdf.open(pdf_path) as parsed_document:
        assert parsed_document.page_count == 1

        text = parsed_document[0].get_text()

        assert "Supplier must provide evidence" in text
