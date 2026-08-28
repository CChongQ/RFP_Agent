from sqlalchemy import Engine, create_engine

from app.core.config import get_settings


def create_database_engine() -> Engine:
    settings = get_settings()

    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
    )