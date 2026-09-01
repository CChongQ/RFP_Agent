from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_analysis_runner, get_tender_catalog
from app.api.error_mapping import to_http_exception
from app.schemas import AnalysisResult, CreateAnalysisRequest
from app.services.configured_analysis import AnalysisRunner
from app.services.tender_catalog import TenderCatalog

router = APIRouter(prefix="/analyses", tags=["analyses"])


@router.post("", response_model=AnalysisResult, status_code=status.HTTP_200_OK)
def create_analysis(
    request: CreateAnalysisRequest,
    catalog: Annotated[TenderCatalog, Depends(get_tender_catalog)],
    runner: Annotated[AnalysisRunner, Depends(get_analysis_runner)],
) -> AnalysisResult:
    """Run the complete synchronous analysis"""

    try:
        tender, pdf_path = catalog.get(request.tender_id)
        return runner.run(tender, pdf_path)
    except Exception as exc:
        http_error = to_http_exception(exc)
        if http_error is None:
            raise
        raise http_error from exc
