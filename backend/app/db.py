from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


_cfg = get_settings()
# Sized for fleet-wide polling: each poll holds a connection only briefly, before and after its HTTP call,
# and the poller's own concurrency limit (poll_concurrency) keeps the number of waiting polls bounded.
engine = create_async_engine(_cfg.database_url, pool_size=_cfg.db_pool_size, max_overflow=_cfg.db_max_overflow,
                             pool_timeout=60, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session():
    async with SessionLocal() as session:
        yield session
