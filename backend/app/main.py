from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import APP_VERSION, DEFAULT_APP_NAME


def create_app() -> FastAPI:

    application = FastAPI(
        title=DEFAULT_APP_NAME,
        version=APP_VERSION,
    )
    application.include_router(api_router)
    return application


app = create_app()
