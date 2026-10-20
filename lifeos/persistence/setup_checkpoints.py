"""Run only with the migration role, after Alembic."""

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from lifeos.core.config import Settings


async def main():
    settings = Settings()
    if settings.migration_database_url is None:
        raise RuntimeError("MIGRATION_DATABASE_URL is required")
    url = settings.migration_database_url.get_secret_value().replace(
        "postgresql+psycopg://", "postgresql://"
    )
    async with AsyncConnectionPool(
        url,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=lifeos",
        },
    ) as pool:
        await AsyncPostgresSaver(pool).setup()


if __name__ == "__main__":
    asyncio.run(main())
