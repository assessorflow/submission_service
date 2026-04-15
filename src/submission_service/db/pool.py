"""asyncpg connection pool for af_submission database."""

from __future__ import annotations

import asyncpg
import structlog

from submission_service import config

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=config.DB_HOST,
            port=config.DB_PORT,
            database=config.DB_NAME,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            min_size=2,
            max_size=10,
        )
        logger.info(
            "db_pool_created",
            host=config.DB_HOST,
            port=config.DB_PORT,
            database=config.DB_NAME,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("db_pool_closed")
