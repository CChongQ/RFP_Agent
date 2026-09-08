from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import APP_VERSION, DEFAULT_APP_NAME, get_settings
from app.services.analysis_progress import configure_analysis_progress_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_analysis_progress_logging(settings.analysis_progress_log_path)
    application = FastAPI(
        title=DEFAULT_APP_NAME,
        version=APP_VERSION,
    )
    application.include_router(api_router)
    return application


app = create_app()
