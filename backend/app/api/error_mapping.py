from dataclasses import dataclass

from fastapi import HTTPException, status
from openai import OpenAIError

from app.services.analysis_service import AnalysisServiceError
from app.services.configured_analysis import (
    AnalysisConfigurationError,
    AnalysisDatabaseError,
    CompanyEvidenceMissingError,
)
from app.services.decision_service import DecisionServiceError
from app.services.pdf_extractor import PdfExtractionError
from app.services.requirement_extractor import RequirementExtractionError
from app.services.tender_catalog import (
    TenderCatalogError,
    TenderNotFoundError,
    TenderSourceMissingError,
)


@dataclass(frozen=True)
class ApiErrorResponse:
    status_code: int
    detail: str


MODEL_FAILURE_RESPONSE = ApiErrorResponse(
    status.HTTP_502_BAD_GATEWAY,
    "The analysis model could not produce a valid result",
)


# Keep public messages here, internal paths, model output, and DB details stay private
ERROR_RESPONSES: tuple[tuple[type[Exception], ApiErrorResponse], ...] = (
    (
        TenderNotFoundError,
        ApiErrorResponse(
            status.HTTP_404_NOT_FOUND,
            "The requested accepted tender was not found",
        ),
    ),
    (
        TenderSourceMissingError,
        ApiErrorResponse(
            status.HTTP_404_NOT_FOUND, 
            "The tender PDF is missing"),
    ),
    (
        TenderCatalogError,
        ApiErrorResponse(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The tender catalog is unavailable",
        ),
    ),
    (
        CompanyEvidenceMissingError,
        ApiErrorResponse(
            status.HTTP_409_CONFLICT,
            "Company evidence has not been seeded",
        ),
    ),
    (
        AnalysisConfigurationError,
        ApiErrorResponse(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Analysis model configuration is unavailable",
        ),
    ),
    (
        AnalysisDatabaseError,
        ApiErrorResponse(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Analysis database is unavailable",
        ),
    ),
    (
        PdfExtractionError,
        ApiErrorResponse(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The tender PDF could not be analyzed",
        ),
    ),
    (
        AnalysisServiceError,
        ApiErrorResponse(
            status.HTTP_409_CONFLICT,
            "The tender document does not match its manifest record",
        ),
    ),
    (RequirementExtractionError, MODEL_FAILURE_RESPONSE),
    (DecisionServiceError, MODEL_FAILURE_RESPONSE),
    (OpenAIError, MODEL_FAILURE_RESPONSE),
)

def to_http_exception(error: Exception) -> HTTPException | None:
    """Map one known internal error to its safe public response"""

    for error_type, response in ERROR_RESPONSES:
        if isinstance(error, error_type):
            return HTTPException(
                status_code=response.status_code,
                detail=response.detail,
            )
    return None
