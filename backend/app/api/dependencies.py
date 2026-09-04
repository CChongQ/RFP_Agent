from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.database.session import create_session_factory
from app.services.analysis_precheck import (
    AnalysisPrecheckRunner,
    AnalysisPrecheckService,
)
from app.services.configured_analysis import AnalysisRunner, ConfiguredAnalysisRunner
from app.services.tender_catalog import TenderCatalog


@lru_cache
def get_api_session_factory() -> sessionmaker[Session]:
    # Reuse the engine and connection pool across requests.
    return create_session_factory()


def get_database_session() -> Iterator[Session]:
    factory = get_api_session_factory()
    
    # Always close the request session, including after an error.
    with factory() as session:
        yield session


def get_tender_catalog(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TenderCatalog:
    # Read paths from settings so local and hosted setups can differ.
    return TenderCatalog(settings.tender_manifest_path, settings.tender_raw_dir)


def get_analysis_runner(
    session: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnalysisRunner:
    return ConfiguredAnalysisRunner(session, settings)


def get_analysis_precheck_runner(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnalysisPrecheckRunner:
    return AnalysisPrecheckService(
        max_pdf_mb=settings.max_pdf_mb,
        max_pdf_pages=settings.max_pdf_pages,
        page_threshold=settings.analysis_page_threshold,
    )
