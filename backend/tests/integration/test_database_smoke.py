"""Check DB flow: connect to PostgreSQL, find pgvector, and execute a vector-distance query."""

import pytest
from sqlalchemy import text

from app.database.session import create_database_engine


@pytest.mark.integration
def test_postgresql_and_pgvector_are_available() -> None:
    # build an engine from the DATABASE_URL loaded by application settings
    engine = create_database_engine()

    try:
        # verify SQL connectivity, extension installation, and vector operations
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

            # pgvector is installed and Euclidean distance is computed correctly
            assert vector_version
            assert float(distance) == pytest.approx(1.0)
    finally:
        # Always release pooled connections, including when an assertion fails
        engine.dispose()
