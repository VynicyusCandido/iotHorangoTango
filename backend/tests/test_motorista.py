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


async def test_tag_de_carro_que_saiu_nao_vale_para_o_proximo(app, cliente):
    await receber(app, *status_online())
    # Carro A, com tag, estaciona e sai
    await receber(app, *estado(1, "livre", "2026-11-12T10:00:00Z", 80.0))
    await receber(app, *rfid(1, "04A1B2C3", "2026-11-12T10:01:00Z"))
    await receber(app, *estado(1, "ocupada", "2026-11-12T10:01:03Z"))
    await receber(app, *estado(1, "livre", "2026-11-12T11:00:00Z", 80.0))
    # A tag é lida de novo quando o carro A passa sobre o leitor ao sair
    await receber(app, *rfid(1, "04A1B2C3", "2026-11-12T11:00:02Z"))
    # Carro B, sem tag, ocupa a vaga mais tarde
    await receber(app, *estado(1, "ocupada", "2026-11-12T11:30:00Z"))
    assert (await cliente.get("/api/minha-vaga/04A1B2C3")).status_code == 404


async def test_tag_de_quem_saiu_nao_vale_para_quem_chega_logo_depois(app, cliente):
    """Dentro da margem de 60 s, mas antes de a vaga ficar livre: é do carro que saiu."""
    await receber(app, *status_online())
    await receber(app, *estado(1, "livre", "2026-11-24T22:29:00Z", 80.0))
    await receber(app, *rfid(1, "532B2F01140001", "2026-11-24T22:30:02Z"))
    await receber(app, *estado(1, "ocupada", "2026-11-24T22:30:09Z"))
    assert (await cliente.get("/api/minha-vaga/532B2F01140001")).status_code == 200
    await receber(app, *estado(1, "livre", "2026-11-24T22:30:29Z", 80.0))
    # Outro carro, sem tag, ocupa 32 s depois da leitura
    await receber(app, *estado(1, "ocupada", "2026-11-24T22:30:34Z"))
    assert (await cliente.get("/api/minha-vaga/532B2F01140001")).status_code == 404


async def test_republicar_ocupada_nao_reinicia_a_ocupacao(app, cliente):
    await receber(app, *status_online())
    await receber(app, *rfid(1, "04A1B2C3", "2026-11-12T10:00:00Z"))
    await receber(app, *estado(1, "ocupada", "2026-11-12T10:00:03Z"))
    # ESP32 reconecta e republica o mesmo estado bem depois
    await receber(app, *estado(1, "ocupada", "2026-11-12T12:00:00Z"))
    r = await cliente.get("/api/minha-vaga/04A1B2C3")
    assert r.status_code == 200 and r.json()["vaga"] == 1


async def test_falha_do_sensor_nao_encerra_a_ocupacao(app, cliente):
    await receber(app, *status_online())
    await receber(app, *rfid(1, "04A1B2C3", "2026-11-12T10:00:00Z"))
    await receber(app, *estado(1, "ocupada", "2026-11-12T10:00:03Z"))
    await receber(app, *estado(1, "erro", "2026-11-12T10:30:00Z", None))
    assert (await cliente.get("/api/minha-vaga/04A1B2C3")).status_code == 404  # estado incerto
    await receber(app, *estado(1, "ocupada", "2026-11-12T10:31:00Z"))
    r = await cliente.get("/api/minha-vaga/04A1B2C3")
    assert r.status_code == 200 and r.json()["vaga"] == 1


async def test_mensagem_invalida_e_ignorada(app, cliente):
    await receber(app, "garagem/vaga/1", b"livre")
    assert (await cliente.get("/api/vagas")).json()["total"] == 0
