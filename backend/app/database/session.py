from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def create_database_engine(database_url: str | None = None) -> Engine:
    
    resolved_url = database_url or get_settings().database_url

    return create_engine(
        resolved_url,
        # Detect stale pooled connections before a workflow starts
        pool_pre_ping=True,
    )


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create sessions that retain loaded values after transaction commits"""

    # Keep loaded values usable while the API serializes a committed result
    resolved_engine = engine or create_database_engine()
    
    return sessionmaker(bind=resolved_engine, expire_on_commit=False)
