import csv
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from app.schemas import TenderDocument

REQUIRED_COLUMNS = {
    "tender_id",
    "title",
    "notice_url",
    "local_filename",
    "sha256",
    "selection_status",
}

class TenderCatalogError(RuntimeError):
    """when the tender manifest is missing or malformed"""


class TenderNotFoundError(LookupError):
    """when an accepted tender ID is not in the manifest"""


class TenderSourceMissingError(FileNotFoundError):
    """when a manifest tender does not resolve to a local PDF"""


def _field(row: Mapping[object, object], name: str) -> str:
    # Fail early when a required CSV value is blank or not text
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise TenderCatalogError(f"accepted tender has an invalid {name} value")
    return value.strip()


class TenderCatalog:
    """Loads accepted tender metadata and safely resolves its local PDF"""

    def __init__(self, manifest_path: Path, raw_directory: Path) -> None:
        self._manifest_path = Path(manifest_path)
        self._raw_directory = Path(raw_directory)

    def get(self, tender_id: str) -> tuple[TenderDocument, Path]:
        requested_id = tender_id.strip()
        if not requested_id:
            raise ValueError("tender_id cannot be empty")
        
        rows = self._read_manifest_rows()
        row = self._find_accepted_row(rows, requested_id)
        
        tender = self._build_tender(row)
        
        return tender, self._resolve_pdf_path(tender)

    def _read_manifest_rows(self) -> list[dict[str, str]]:
        if not self._manifest_path.is_file():
            raise TenderCatalogError("tender manifest is unavailable")
        try:
            # utf-8-sig also accepts CSV files saved with an Excel BOM
            with self._manifest_path.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as manifest_file:
                reader = csv.DictReader(manifest_file)
                self._validate_columns(reader.fieldnames)
                return [
                    {
                        key: value
                        for key, value in row.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
                    for row in reader
                ]
        except (OSError, csv.Error) as exc:
            raise TenderCatalogError("tender manifest could not be read") from exc

    @staticmethod
    def _validate_columns(fieldnames: list[str] | None) -> None:
        missing_columns = REQUIRED_COLUMNS - set(fieldnames or [])
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise TenderCatalogError(
                f"tender manifest is missing required columns: {missing}"
            )

    @staticmethod
    def _find_accepted_row(
        rows: list[dict[str, str]],
        tender_id: str,
    ) -> dict[str, str]:
        matching_rows = [
            row
            for row in rows
            if _field(row, "tender_id") == tender_id
            and _field(row, "selection_status").casefold() == "accepted"
        ]
        if not matching_rows:
            raise TenderNotFoundError(tender_id)
        if len(matching_rows) > 1:
            raise TenderCatalogError("tender manifest contains a duplicate accepted ID")
        return matching_rows[0]

    @staticmethod
    def _build_tender(row: Mapping[object, object]) -> TenderDocument:
        try:
            return TenderDocument(
                tender_id=_field(row, "tender_id"),
                title=_field(row, "title"),
                source_url=_field(row, "notice_url"),
                file_hash=_field(row, "sha256"),
                local_filename=_field(row, "local_filename"),
            )
        except ValidationError as exc:
            raise TenderCatalogError("accepted tender metadata is invalid") from exc

    def _resolve_pdf_path(self, tender: TenderDocument) -> Path:
        raw_directory = self._raw_directory.resolve()
        pdf_path = (raw_directory / tender.local_filename).resolve()
        # Stop filenames such as ../secret.pdf from leaving the data folder.
        if not pdf_path.is_relative_to(raw_directory):
            raise TenderCatalogError("tender filename resolves outside the raw directory")
        if not pdf_path.is_file():
            raise TenderSourceMissingError(tender.tender_id)
        return pdf_path
