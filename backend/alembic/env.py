import asyncio
from logging.config import fileConfig

import pgvector.sqlalchemy  # noqa: F401  注册 Vector 类型，让 alembic 看见
from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.db import models  # noqa: F401  触发 4 个模型注册到 Base.metadata
from app.db.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline mode unsupported in async setup")

asyncio.run(run_async_migrations())
