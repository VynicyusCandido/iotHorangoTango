"""Testes rodam com SQLite em arquivo temporário: sem Postgres nem broker."""
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from comum.modelos import Base

DEVICE = "esp32-garagem-240ac4000110"
BASE = f"garagem/v1/{DEVICE}"


@pytest.fixture
async def sessoes(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'teste.db'}")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def carga(**campos) -> bytes:
    return json.dumps(campos).encode()


def status_online(ts="2026-11-12T10:00:00Z"):
    return f"{BASE}/status", carga(v=1, deviceId=DEVICE, ts=ts, status="online", firmware="0.10.0", vagas=[1, 2])


def estado(vaga, valor, ts, dist=12.4):
    distancia = None if dist is None else {"valor": dist, "unidade": "cm"}
    return f"{BASE}/vaga/{vaga}/estado", carga(
        v=1, deviceId=DEVICE, ts=ts, vaga=vaga, estado=valor, distancia=distancia
    )


def ack(command_id, resultado, ts="2026-11-12T10:01:00Z", erro=None):
    campos = dict(v=1, deviceId=DEVICE, ts=ts, commandId=command_id, resultado=resultado)
    if erro:
        campos["erro"] = erro
    return f"{BASE}/ack", carga(**campos)


def rfid(vaga, uid, ts):
    return f"{BASE}/rfid", carga(v=1, deviceId=DEVICE, ts=ts, vaga=vaga, uid=uid)
