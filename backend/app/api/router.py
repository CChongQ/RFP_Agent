from fastapi import APIRouter

from app.api.routes import analyses

API_V1_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_V1_PREFIX)

api_router.include_router(analyses.router)
