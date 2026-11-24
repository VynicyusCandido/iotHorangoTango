"""Reserva pelo app do motorista: regras no operador e repasse pelo motorista."""
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from comum import modelos as m
from motorista.app import criar_app as criar_motorista
from persistencia.gravador import Gravador
from tests.conftest import DEVICE, ack, estado
from tests.test_operador import dispositivo_online

# Fixtures cliente/publicador vêm de test_operador
from tests.test_operador import cliente, publicador  # noqa: F401

TAG = "53C83301140001"
URL = "/api/motorista/reservas"


async def cadastrar_tag(sessoes, uid=TAG):
    async with sessoes() as s, s.begin():
        s.add(m.Tag(uid=uid, apelido="carro de teste", criada_em=datetime.now(timezone.utc)))


async def test_reserva_com_tag_cadastrada_publica_o_comando(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    await cadastrar_tag(sessoes)
    r = await cliente.post(URL, json={"uid": TAG.lower(), "vaga": 1})
    assert r.status_code == 202, r.text
    corpo = r.json()
    assert corpo["status"] == "enviado" and corpo["vaga"] == 1
    assert "origem" not in corpo
    [(topico, payload)] = publicador.enviados
    assert topico == f"garagem/v1/{DEVICE}/cmd"
    assert json.loads(payload)["acao"] == "reservar" and "duracao_min" not in json.loads(payload)
    async with sessoes() as s:
        cmd = await s.scalar(select(m.Comando))
        assert cmd.origem == f"tag:{TAG}"


async def test_tag_nao_cadastrada_nao_reserva(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    r = await cliente.post(URL, json={"uid": TAG, "vaga": 1})
    assert r.status_code == 403
    assert publicador.enviados == []


@pytest.mark.parametrize("corpo", [{"uid": "XYZ", "vaga": 1}, {"uid": TAG, "vaga": 0}, {"uid": TAG}])
async def test_pedido_invalido(cliente, sessoes, publicador, corpo):
    await dispositivo_online(sessoes)
    await cadastrar_tag(sessoes)
    assert (await cliente.post(URL, json=corpo)).status_code == 422
    assert publicador.enviados == []


async def test_so_vaga_livre_e_existente(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    await cadastrar_tag(sessoes)
    g = Gravador(sessoes)
    await g.tratar(*estado(2, "ocupada", "2026-11-12T10:00:00Z"))
    assert (await cliente.post(URL, json={"uid": TAG, "vaga": 2})).status_code == 409
    assert (await cliente.post(URL, json={"uid": TAG, "vaga": 3})).status_code == 404
    assert publicador.enviados == []


async def test_uma_reserva_ativa_por_tag(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    await cadastrar_tag(sessoes)
    g = Gravador(sessoes)
    await g.tratar(*estado(2, "livre", "2026-11-12T10:00:00Z", 80.0))
    cid = (await cliente.post(URL, json={"uid": TAG, "vaga": 1})).json()["command_id"]
    # Ainda sem resposta do firmware: segunda reserva recusada
    assert (await cliente.post(URL, json={"uid": TAG, "vaga": 2})).status_code == 409
    # Firmware recusou: a tag pode tentar de novo
    await g.tratar(*ack(cid, "rejeitado", erro="VAGA_OCUPADA"))
    assert (await cliente.post(URL, json={"uid": TAG, "vaga": 2})).status_code == 202


async def test_reserva_confirmada_segura_a_tag_ate_a_vaga_sair_de_reservada(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    await cadastrar_tag(sessoes)
    g = Gravador(sessoes)
    await g.tratar(*estado(2, "livre", "2026-11-12T10:00:00Z", 80.0))
    cid = (await cliente.post(URL, json={"uid": TAG, "vaga": 1})).json()["command_id"]
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await g.tratar(*ack(cid, "aceito", ts=agora))
    await g.tratar(*estado(1, "reservada", agora, 80.0))
    await g.tratar(*ack(cid, "executado", ts=agora))
    assert (await cliente.get(f"{URL}/{cid}")).json()["status"] == "confirmado"
    assert (await cliente.post(URL, json={"uid": TAG, "vaga": 2})).status_code == 409
    # Reserva expirou ou o carro chegou: a vaga sai de reservada
    await g.tratar(*estado(1, "ocupada", agora))
    assert (await cliente.post(URL, json={"uid": TAG, "vaga": 2})).status_code == 202


async def test_consulta_so_mostra_reservas_do_motorista(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    assert (await cliente.get(f"{URL}/c-000000000000")).status_code == 404


async def test_dispositivo_offline_nao_reserva(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    await cadastrar_tag(sessoes)
    async with sessoes() as s, s.begin():
        (await s.get(m.Dispositivo, DEVICE)).online = False
    assert (await cliente.post(URL, json={"uid": TAG, "vaga": 1})).status_code == 409


# ----------------------------------------------------------------------
# Motorista: repassa ao operador
# ----------------------------------------------------------------------


def transporte(cliente_operador):
    """Encaminha as chamadas do motorista para o app do operador de teste."""

    async def tratar(req: httpx.Request) -> httpx.Response:
        r = await cliente_operador.request(req.method, req.url.path, content=req.content, headers=req.headers)
        return httpx.Response(r.status_code, content=r.content, headers=r.headers)

    return httpx.MockTransport(tratar)


async def test_motorista_repassa_a_reserva(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    await cadastrar_tag(sessoes)
    app = criar_motorista(iniciar_mqtt=False, operador=transporte(cliente))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste") as c:
        r = await c.post("/api/reservas", json={"uid": TAG.lower(), "vaga": 1, "deviceId": DEVICE})
        assert r.status_code == 202, r.text
        corpo = r.json()
        assert set(corpo) == {"commandId", "deviceId", "vaga", "status", "erro"}
        assert (await c.get(f"/api/reservas/{corpo['commandId']}")).json()["status"] == "enviado"
        r = await c.post("/api/reservas", json={"uid": "AABBCCDD", "vaga": 1})
        assert r.status_code == 403 and "não cadastrada" in r.json()["detail"]
    assert len(publicador.enviados) == 1


async def test_motorista_sem_operador_responde_503():
    def recusar(req):
        raise httpx.ConnectError("fora", request=req)

    app = criar_motorista(iniciar_mqtt=False, operador=httpx.MockTransport(recusar))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste") as c:
        assert (await c.post("/api/reservas", json={"uid": TAG, "vaga": 1})).status_code == 503
