import pytest
from sqlalchemy import text

from app.database.session import create_database_engine

"""
Test the PostgreSQL connection and pgvector setup.
"""

# Basic tests
@pytest.mark.integration
def test_postgresql_and_pgvector_are_available() -> None:
    # Build the engine from the same setting used by the application.
    engine = create_database_engine()

    try:
        # Check the connection, pgvector extension, and one distance query.
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1

            vector_version = connection.execute(
                text(
                    "SELECT extversion "
                    "FROM pg_extension "
                    "WHERE extname = 'vector'"
                )
            ).scalar_one()

            distance = connection.execute(
                text(
                    "SELECT "
                    "'[1,2,3]'::vector <-> '[1,2,4]'::vector"
                )
            ).scalar_one()

            assert vector_version
            assert float(distance) == pytest.approx(1.0)
    finally:
        engine.dispose()

