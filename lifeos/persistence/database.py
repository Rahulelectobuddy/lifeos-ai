from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lifeos.core.config import Settings


def create_database(settings: Settings):
    engine = create_async_engine(
        settings.database_url.get_secret_value(), pool_pre_ping=True, pool_size=8, max_overflow=4
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
