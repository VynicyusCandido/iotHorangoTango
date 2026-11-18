"""Assinante do motorista: contagem, privacidade, tags e tempo real."""
import time

import pytest
from fastapi.testclient import TestClient

from comum import contrato
from motorista.app import criar_app
from motorista.garagem import Garagem
from tests.conftest import BASE, DEVICE, carga, estado, rfid, status_online
from tests.test_motorista import receber

# Fixtures app/cliente vêm de test_motorista
from tests.test_motorista import app, cliente  # noqa: F401

OUTRO = "esp32-garagem-aabbccddeeff"


def msg(topico, payload):
    return contrato.interpretar(topico, payload)


def estado_outro(vaga, valor, ts):
    return f"garagem/v1/{OUTRO}/vaga/{vaga}/estado", carga(
        v=1, deviceId=OUTRO, ts=ts, vaga=vaga, estado=valor,
        distancia=None if valor == "erro" else {"valor": 50.0, "unidade": "cm"},
    )


# ----------------------------------------------------------------------
# Garagem (estado em memória)
# ----------------------------------------------------------------------


def test_aplicar_so_avisa_quando_o_motorista_ve_diferenca():
    g = Garagem()
    assert g.aplicar(msg(*estado(1, "livre", "2026-11-12T10:00:00Z", 80.0))) is True
    assert g.aplicar(msg(*estado(1, "livre", "2026-11-12T10:03:00Z", 81.0))) is False  # republicação
    assert g.aplicar(msg(*estado(1, "ocupada", "2026-11-12T10:05:00Z"))) is True
    assert g.aplicar(msg(*status_online())) is True
    assert g.aplicar(msg(*status_online())) is False
    assert g.aplicar(msg(*rfid(1, "04A1B2C3", "2026-11-12T10:05:01Z"))) is False  # tag não muda a lista


def test_reservada_e_erro_nao_contam_como_livre():
    g = Garagem()
    g.aplicar(msg(*estado(1, "reservada", "2026-11-12T10:00:00Z", 80.0)))
    g.aplicar(msg(*estado(2, "erro", "2026-11-12T10:00:00Z", dist=None)))
    r = g.resumo()
    assert (r["livres"], r["total"]) == (0, 2)
    assert [v["estado"] for v in r["vagas"]] == ["reservada", "erro"]


def test_varios_dispositivos_ordenados_e_offline_isolado():
    g = Garagem()
    g.aplicar(msg(*estado_outro(1, "livre", "2026-11-12T10:00:00Z")))
    g.aplicar(msg(*estado(2, "livre", "2026-11-12T10:00:00Z", 80.0)))
    g.aplicar(msg(*estado(1, "livre", "2026-11-12T10:00:00Z", 80.0)))
    g.aplicar(msg(f"garagem/v1/{OUTRO}/status", carga(v=1, deviceId=OUTRO, status="offline")))
    r = g.resumo()
    assert [(v["deviceId"], v["vaga"], v["estado"]) for v in r["vagas"]] == [
        (DEVICE, 1, "livre"),
        (DEVICE, 2, "livre"),
        (OUTRO, 1, "sem_dados"),
    ]
    assert r["livres"] == 2


def test_desde_e_o_ts_do_estado():
    g = Garagem()
    g.aplicar(msg(*estado(1, "ocupada", "2026-11-12T10:00:00Z")))
    assert g.resumo()["vagas"][0]["desde"] == "2026-11-12T10:00:00+00:00"
    g.aplicar(msg(f"{BASE}/vaga/2/estado", carga(
        v=1, deviceId=DEVICE, ts=None, ts_sincronizado=False, uptime_ms=10, vaga=2, estado="livre",
        distancia={"valor": 80.0, "unidade": "cm"},
    )))
    assert g.resumo()["vagas"][1]["desde"] is None


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------


async def test_uid_mal_formado_e_recusado(cliente):
    for uid in ("xyz", "04A1B2", "04-A1-B2-C3", "04A1B2C3D"):
        assert (await cliente.get(f"/api/minha-vaga/{uid}")).status_code == 422


async def test_tag_mais_recente_na_vaga_vence(app, cliente):
    await receber(app, *status_online())
    await receber(app, *estado(1, "ocupada", "2026-11-12T10:00:10Z"))
    await receber(app, *rfid(1, "04A1B2C3", "2026-11-12T10:00:07Z"))
    await receber(app, *rfid(1, "0A0B0C0D", "2026-11-12T10:30:07Z"))  # outro carro na mesma vaga
    assert (await cliente.get("/api/minha-vaga/04A1B2C3")).status_code == 404
    assert (await cliente.get("/api/minha-vaga/0A0B0C0D")).json()["vaga"] == 1


async def test_carro_some_quando_o_dispositivo_cai(app, cliente):
    await receber(app, *status_online())
    await receber(app, *rfid(1, "04A1B2C3", "2026-11-12T10:00:07Z"))
    await receber(app, *estado(1, "ocupada", "2026-11-12T10:00:10Z"))
    assert (await cliente.get("/api/minha-vaga/04A1B2C3")).status_code == 200
    await receber(app, f"{BASE}/status", carga(v=1, deviceId=DEVICE, status="offline"))
    assert (await cliente.get("/api/minha-vaga/04A1B2C3")).status_code == 404


async def test_lista_de_vagas_nao_expoe_tags_nem_distancias(app, cliente):
    await receber(app, *status_online())
    await receber(app, *estado(1, "ocupada", "2026-11-12T10:00:10Z", dist=11.5))
    await receber(app, *rfid(1, "04A1B2C3", "2026-11-12T10:00:07Z"))
    texto = (await cliente.get("/api/vagas")).text
    assert "04A1B2C3" not in texto and "11.5" not in texto and "distancia" not in texto


# ----------------------------------------------------------------------
# Tempo real (WebSocket) e saúde
# ----------------------------------------------------------------------


@pytest.fixture
def app_ws():
    return criar_app(iniciar_mqtt=False)


def test_ws_envia_resumo_ao_conectar_e_a_cada_mudanca(app_ws):
    with TestClient(app_ws) as c:
        c.portal.call(app_ws.state.ao_receber, *estado(1, "livre", "2026-11-12T10:00:00Z", 80.0), True)
        with c.websocket_connect("/ws") as ws:
            inicial = ws.receive_json()
            assert inicial["tipo"] == "vagas" and inicial["livres"] == 1

            for _ in range(200):  # o WebSocket entra no difusor logo após o resumo
                if app_ws.state.difusor.conectados:
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("WebSocket não se registrou no difusor")
            receber_ws = app_ws.state.ao_receber
            c.portal.call(receber_ws, *estado(1, "livre", "2026-11-12T10:03:00Z", 80.0), False)  # repetido
            c.portal.call(receber_ws, *rfid(1, "04A1B2C3", "2026-11-12T10:04:00Z"), False)       # tag
            c.portal.call(receber_ws, *estado(1, "ocupada", "2026-11-12T10:05:00Z"), False)
            mudanca = ws.receive_json()
    assert mudanca["livres"] == 0 and mudanca["vagas"][0]["estado"] == "ocupada"
    assert "04A1B2C3" not in str(mudanca)


def test_saude_informa_o_broker(app_ws):
    with TestClient(app_ws) as c:
        assert c.get("/saude").json() == {"servico": "motorista", "broker": False}
