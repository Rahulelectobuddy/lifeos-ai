import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from lifeos.core.config import Settings
from lifeos.persistence.models import Base

settings = Settings()
if settings.migration_database_url is None:
    raise RuntimeError("MIGRATION_DATABASE_URL is required; never use runtime credentials for DDL")
url = settings.migration_database_url.get_secret_value()


async def migrate():
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:

        def run(sync_connection):
            context.configure(
                connection=sync_connection,
                target_metadata=Base.metadata,
                include_schemas=True,
                version_table_schema="lifeos",
            )
            with context.begin_transaction():
                context.run_migrations()

        # The administrator provisions this schema. Even CREATE SCHEMA IF NOT
        # EXISTS requires database-wide CREATE, which the migration role lacks.
        schema = await connection.exec_driver_sql(
            "SELECT 1 FROM pg_namespace WHERE nspname = 'lifeos'"
        )
        if schema.scalar_one_or_none() is None:
            raise RuntimeError(
                "Provision the lifeos schema with infrastructure/provision_roles.sql first"
            )
        await connection.commit()
        await connection.run_sync(run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=url, target_metadata=Base.metadata, literal_binds=True, version_table_schema="lifeos"
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(migrate())
