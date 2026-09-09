from pydantic import BaseModel, ConfigDict, PositiveInt


class RunSettings(BaseModel):
    """note: settings controlled by the analysis service that can affect a run"""

    model_config = ConfigDict(extra="forbid")

    max_pdf_mb: PositiveInt
    max_pdf_pages: PositiveInt
    max_chunk_characters: PositiveInt
