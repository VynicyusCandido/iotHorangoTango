"""API do operador: login, perfis e envio de comando (sem broker: publicar falso)."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from comum import contrato, modelos as m
from operador import auth
from operador.app import criar_app
from persistencia.gravador import Gravador
from tests.conftest import BASE, DEVICE, estado, status_online

SEGREDO = "segredo-de-teste"


class PublicadorFalso:
    def __init__(self):
        self.enviados = []
        self.falhar = False

    async def __call__(self, topico, payload):
        if self.falhar:
            raise RuntimeError("broker fora")
        self.enviados.append((topico, payload))


@pytest.fixture
async def publicador():
    return PublicadorFalso()


@pytest.fixture
async def cliente(sessoes, publicador):
    agora = datetime.now(timezone.utc)
    async with sessoes() as s, s.begin():
        s.add(m.Usuario(nome="admin", senha_hash=auth.hash_senha("senha-admin"), perfil="administrador", criado_em=agora))
        s.add(m.Usuario(nome="op", senha_hash=auth.hash_senha("senha-operador"), perfil="operador", criado_em=agora))
    app = criar_app(sessoes, jwt_segredo=SEGREDO, publicar=publicador, iniciar_mqtt=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste") as c:
        yield c


async def entrar(cliente, usuario, senha):
    r = await cliente.post("/api/login", json={"usuario": usuario, "senha": senha})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def dispositivo_online(sessoes):
    g = Gravador(sessoes)
    await g.tratar(*status_online())
    await g.tratar(*estado(1, "livre", "2026-11-12T10:00:00Z", 80.0))


async def test_login_errado_e_rota_sem_token(cliente):
    r = await cliente.post("/api/login", json={"usuario": "admin", "senha": "errada"})
    assert r.status_code == 401
    r = await cliente.post("/api/login", json={"usuario": "ninguem", "senha": "x"})
    assert r.status_code == 401
    assert (await cliente.get("/api/vagas")).status_code == 401
    r = await cliente.get("/api/vagas", headers={"Authorization": "Bearer lixo"})
    assert r.status_code == 401


async def test_operador_ve_vagas_mas_nao_envia_comando(cliente, sessoes):
    await dispositivo_online(sessoes)
    cab = await entrar(cliente, "op", "senha-operador")
    r = await cliente.get("/api/vagas", headers=cab)
    assert r.status_code == 200
    vagas = r.json()
    assert [(v["vaga"], v["estado"], v["online"]) for v in vagas] == [(1, "livre", True), (2, None, True)]

    r = await cliente.post("/api/comandos", headers=cab, json={"acao": "reservar", "device_id": DEVICE, "vaga": 1})
    assert r.status_code == 403


async def test_admin_envia_reserva_no_formato_do_contrato(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    cab = await entrar(cliente, "admin", "senha-admin")
    r = await cliente.post(
        "/api/comandos", headers=cab, json={"acao": "reservar", "device_id": DEVICE, "vaga": 1, "duracao_min": 2}
    )
    assert r.status_code == 202, r.text
    cmd = r.json()
    assert cmd["status"] == "enviado" and cmd["origem"] == "admin"

    topico, payload = publicador.enviados[0]
    assert topico == f"{BASE}/cmd"
    publicado = contrato.interpretar(topico, payload.encode()).dados
    assert publicado.commandId == cmd["command_id"]
    assert (publicado.acao, publicado.vaga, publicado.duracao_min) == ("reservar", 1, 2)
    assert json.loads(payload)["ts"].endswith("Z")

    r = await cliente.get(f"/api/comandos/{cmd['command_id']}", headers=cab)
    assert r.json()["status"] == "enviado"


async def test_configurar_valida_faixas_antes_de_publicar(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    cab = await entrar(cliente, "admin", "senha-admin")
    base = {"acao": "configurar", "device_id": DEVICE, "duracao_reserva_min": 15}
    r = await cliente.post("/api/comandos", headers=cab, json={**base, "limiar_ocupa_cm": 10, "limiar_libera_cm": 11})
    assert r.status_code == 422
    r = await cliente.post("/api/comandos", headers=cab, json={**base, "limiar_ocupa_cm": 10, "limiar_libera_cm": 13})
    assert r.status_code == 202
    assert json.loads(publicador.enviados[0][1])["limiar_libera_cm"] == 13.0


async def test_comando_recusado_sem_dispositivo_online_ou_vaga(cliente, sessoes):
    cab = await entrar(cliente, "admin", "senha-admin")
    corpo = {"acao": "reservar", "device_id": DEVICE, "vaga": 1}
    assert (await cliente.post("/api/comandos", headers=cab, json=corpo)).status_code == 404

    await dispositivo_online(sessoes)
    corpo["vaga"] = 3
    assert (await cliente.post("/api/comandos", headers=cab, json=corpo)).status_code == 422

    async with sessoes() as s, s.begin():
        (await s.get(m.Dispositivo, DEVICE)).online = False
    corpo["vaga"] = 1
    assert (await cliente.post("/api/comandos", headers=cab, json=corpo)).status_code == 409


async def test_broker_fora_marca_falha_de_envio(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    publicador.falhar = True
    cab = await entrar(cliente, "admin", "senha-admin")
    r = await cliente.post("/api/comandos", headers=cab, json={"acao": "cancelar_reserva", "device_id": DEVICE, "vaga": 1})
    assert r.status_code == 503
    r = await cliente.get("/api/comandos", headers=cab)
    assert r.json()[0]["status"] == "falha_envio"


async def test_admin_cria_usuario(cliente):
    cab = await entrar(cliente, "admin", "senha-admin")
    novo = {"nome": "maria", "senha": "senha-forte-1", "perfil": "operador"}
    assert (await cliente.post("/api/usuarios", headers=cab, json=novo)).status_code == 201
    assert (await cliente.post("/api/usuarios", headers=cab, json=novo)).status_code == 409
    await entrar(cliente, "maria", "senha-forte-1")
