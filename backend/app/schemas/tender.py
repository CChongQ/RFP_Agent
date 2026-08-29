from pydantic import HttpUrl, field_validator

from app.schemas.base import NonEmptyString, SchemaModel, Sha256


class TenderDocument(SchemaModel):
    """tender file info"""

    tender_id: NonEmptyString
    title: NonEmptyString
    source_url: HttpUrl
    file_hash: Sha256
    local_filename: NonEmptyString

    @field_validator("file_hash")
    @classmethod
    def normalize_file_hash(cls, value: str) -> str:
        return value.upper()
