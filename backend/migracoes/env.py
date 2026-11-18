import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from comum.config import obrigatorio
from comum.modelos import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

alvo = Base.metadata


def rodar_offline() -> None:
    context.configure(url=obrigatorio("DATABASE_URL"), target_metadata=alvo, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _rodar(conexao) -> None:
    context.configure(connection=conexao, target_metadata=alvo)
    with context.begin_transaction():
        context.run_migrations()


async def rodar_online() -> None:
    engine = create_async_engine(obrigatorio("DATABASE_URL"))
    async with engine.connect() as conexao:
        await conexao.run_sync(_rodar)
    await engine.dispose()


if context.is_offline_mode():
    rodar_offline()
else:
    asyncio.run(rodar_online())
