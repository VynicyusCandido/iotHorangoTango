"""Assinante do motorista: vagas livres e a vaga do carro pela tag, sem banco."""
import httpx
import pytest

from motorista.app import criar_app
from tests.conftest import BASE, DEVICE, carga, estado, rfid, status_online


@pytest.fixture
async def app():
    return criar_app(iniciar_mqtt=False)


@pytest.fixture
async def cliente(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste") as c:
        yield c


async def receber(app, topico, payload, retido=False):
    await app.state.ao_receber(topico, payload, retido)


async def test_vagas_livres_sem_distancia(app, cliente):
    await receber(app, *status_online(), retido=True)
    await receber(app, *estado(1, "livre", "2026-11-12T10:00:00Z", 80.0), retido=True)
    await receber(app, *estado(2, "ocupada", "2026-11-12T10:00:00Z"), retido=True)
    r = (await cliente.get("/api/vagas")).json()
    assert r["livres"] == 1 and r["total"] == 2
    assert "distancia" not in str(r)


async def test_offline_vira_sem_dados(app, cliente):
    await receber(app, *status_online())
    await receber(app, *estado(1, "livre", "2026-11-12T10:00:00Z", 80.0))
    await receber(app, f"{BASE}/status", carga(v=1, deviceId=DEVICE, status="offline"))
    r = (await cliente.get("/api/vagas")).json()
    assert r["livres"] == 0 and r["vagas"][0]["estado"] == "sem_dados"


async def test_minha_vaga_pela_tag(app, cliente):
    await receber(app, *status_online())
    await receber(app, *rfid(2, "04A1B2C3", "2026-11-12T10:00:07Z"))
    # Tag lida, mas o ultrassônico ainda não confirmou
    await receber(app, *estado(2, "livre", "2026-11-12T10:00:00Z", 80.0))
    assert (await cliente.get("/api/minha-vaga/04a1b2c3")).status_code == 404

    await receber(app, *estado(2, "ocupada", "2026-11-12T10:00:10Z"))
    r = await cliente.get("/api/minha-vaga/04a1b2c3")
    assert r.status_code == 200 and r.json()["vaga"] == 2

    # Carro saiu
    await receber(app, *estado(2, "livre", "2026-11-12T11:00:00Z", 80.0))
    assert (await cliente.get("/api/minha-vaga/04A1B2C3")).status_code == 404


async def test_mensagem_invalida_e_ignorada(app, cliente):
    await receber(app, "garagem/vaga/1", b"livre")
    assert (await cliente.get("/api/vagas")).json()["total"] == 0
