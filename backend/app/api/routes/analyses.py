from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    get_analysis_precheck_runner,
    get_analysis_runner,
    get_tender_catalog,
)
from app.api.error_mapping import to_http_exception
from app.schemas import (
    AnalysisPrecheck,
    AnalysisPrecheckRequest,
    AnalysisResult,
    CreateAnalysisRequest,
)
from app.services.analysis_precheck import AnalysisPrecheckRunner
from app.services.configured_analysis import AnalysisRunner
from app.services.tender_catalog import TenderCatalog

router = APIRouter(prefix="/analyses", tags=["analyses"])


#/analyses/precheck
@router.post(
    "/precheck",
    response_model=AnalysisPrecheck,
    status_code=status.HTTP_200_OK,
)
def precheck_analysis(
    request: AnalysisPrecheckRequest,
    catalog: Annotated[TenderCatalog, Depends(get_tender_catalog)],
    precheck_runner: Annotated[
        AnalysisPrecheckRunner,
        Depends(get_analysis_precheck_runner),
    ],
) -> AnalysisPrecheck:
    """Inspect the selected PDF first"""

    try:
        tender, pdf_path = catalog.get(request.tender_id)
        return precheck_runner.inspect(tender, pdf_path)
    except Exception as exc:
        http_error = to_http_exception(exc)
        if http_error is None:
            raise
        raise http_error from exc


#/analyses
@router.post("", response_model=AnalysisResult, status_code=status.HTTP_200_OK)
def create_analysis(
    request: CreateAnalysisRequest,
    catalog: Annotated[TenderCatalog, Depends(get_tender_catalog)],
    precheck_runner: Annotated[
        AnalysisPrecheckRunner,
        Depends(get_analysis_precheck_runner),
    ],
    runner: Annotated[AnalysisRunner, Depends(get_analysis_runner)],
) -> AnalysisResult:
    """Run the complete synchronous analysis"""

    try:
        tender, pdf_path = catalog.get(request.tender_id)
        precheck = precheck_runner.inspect(tender, pdf_path)
        if precheck.requires_confirmation and not request.confirm_large_document:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "large_document_confirmation_required",
                    "message": (
                        "Review the precheck result, then retry with confirm_large_document=true"
                    ),
                    "precheck": precheck.model_dump(mode="json"),
                },
            )
        return runner.run(tender, pdf_path)
    except Exception as exc:
        http_error = to_http_exception(exc)
        if http_error is None:
            raise
        raise http_error from exc
