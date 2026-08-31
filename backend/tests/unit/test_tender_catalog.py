from pathlib import Path

import pytest

from app.services.tender_catalog import (
    TenderCatalog,
    TenderCatalogError,
    TenderNotFoundError,
    TenderSourceMissingError,
)

"""
Test loading an approved tender and finding its local PDF.
"""

TEST_SHA256 = "A" * 64

def _write_manifest(path: Path, *, local_filename: str = "tender.pdf") -> None:
    path.write_text(
        "tender_id,title,notice_url,local_filename,downloaded_at,sha256,selection_status\n"
        "TENDER-001,Synthetic Tender,https://example.com/tender,"
        f"{local_filename},2026-08-30,{TEST_SHA256},accepted\n",
        encoding="utf-8",
    )


# Basic tests

def test_catalog_loads_accepted_tender_and_resolves_pdf(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    raw_directory = tmp_path / "raw"
    raw_directory.mkdir()
    pdf_path = raw_directory / "tender.pdf"
    pdf_path.write_bytes(b"%PDF synthetic fixture")
    _write_manifest(manifest_path)

    tender, resolved_path = TenderCatalog(manifest_path, raw_directory).get("TENDER-001")

    assert tender.tender_id == "TENDER-001"
    assert tender.file_hash == TEST_SHA256
    assert resolved_path == pdf_path.resolve()


# Corner-case tests

def test_catalog_rejects_unknown_tender(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    raw_directory = tmp_path / "raw"
    raw_directory.mkdir()
    _write_manifest(manifest_path)

    with pytest.raises(TenderNotFoundError):
        TenderCatalog(manifest_path, raw_directory).get("TENDER-999")


def test_catalog_reports_missing_pdf(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    raw_directory = tmp_path / "raw"
    raw_directory.mkdir()
    _write_manifest(manifest_path)

    with pytest.raises(TenderSourceMissingError):
        TenderCatalog(manifest_path, raw_directory).get("TENDER-001")


def test_catalog_rejects_path_outside_raw_directory(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.csv"
    raw_directory = tmp_path / "raw"
    raw_directory.mkdir()
    outside_pdf = tmp_path / "outside.pdf"
    outside_pdf.write_bytes(b"%PDF synthetic fixture")
    _write_manifest(manifest_path, local_filename="../outside.pdf")

    with pytest.raises(TenderCatalogError, match="outside"):
        TenderCatalog(manifest_path, raw_directory).get("TENDER-001")
