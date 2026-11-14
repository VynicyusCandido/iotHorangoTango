"""Conexão com o banco (SQLAlchemy assíncrono)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import obrigatorio


def criar_sessoes(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(url or obrigatorio("DATABASE_URL"), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def inserir_ignorando(sessao: AsyncSession, modelo: Any, valores: dict[str, Any]) -> bool:
    """INSERT ... ON CONFLICT DO NOTHING. Retorna False se já existia."""
    dialeto = sessao.bind.dialect.name
    if dialeto == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialeto == "sqlite":  # testes
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"banco não suportado: {dialeto}")
    resultado = await sessao.execute(insert(modelo).values(**valores).on_conflict_do_nothing())
    return resultado.rowcount == 1
