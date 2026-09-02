from pathlib import Path

import pymupdf

"""
Test that the installed library can read generated PDF text.
Run this before runining the PDF extractot test.  

"""

# Basic tests

def test_pymupdf_extracts_page_text(tmp_path: Path) -> None:
    # Generate a temporary PDF with known text.
    pdf_path = tmp_path / "smoke-test.pdf"

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Supplier must provide evidence of cloud migration experience.",
    )
    document.save(pdf_path)
    document.close()

    # Reopen the saved PDF to check the full library flow.
    with pymupdf.open(pdf_path) as parsed_document:
        assert parsed_document.page_count == 1

        text = parsed_document[0].get_text()

        assert "Supplier must provide evidence" in text
