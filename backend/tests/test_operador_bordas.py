"""Assinante do operador: tokens, filtros, validação de comandos, ciclo completo e tempo real."""
import time
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from comum import modelos as m
from operador import auth
from operador.app import criar_app
from persistencia.gravador import Gravador
from tests.conftest import BASE, DEVICE, ack, carga, estado, rfid, status_online
from tests.test_operador import SEGREDO, PublicadorFalso, dispositivo_online, entrar

# Fixtures cliente/publicador vêm de test_operador
from tests.test_operador import cliente, publicador  # noqa: F401


def cab_token(token):
    return {"Authorization": f"Bearer {token}"}


# ----------------------------------------------------------------------
# Login e perfis
# ----------------------------------------------------------------------


async def test_token_vencido_ou_de_outro_segredo_e_recusado(cliente):
    vencido = pyjwt.encode(
        {"sub": "admin", "perfil": "administrador", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        SEGREDO,
        algorithm="HS256",
    )
    forjado = auth.criar_token("outro-segredo", "admin", "administrador")
    for token in (vencido, forjado):
        assert (await cliente.get("/api/eu", headers=cab_token(token))).status_code == 401


async def test_perfil_vem_do_login(cliente):
    r = await cliente.get("/api/eu", headers=await entrar(cliente, "op", "senha-operador"))
    assert r.json() == {"nome": "op", "perfil": "operador"}


async def test_so_admin_cria_usuario_e_senha_precisa_ser_forte(cliente):
    novo = {"nome": "joao", "senha": "senha-forte-1", "perfil": "operador"}
    op = await entrar(cliente, "op", "senha-operador")
    assert (await cliente.post("/api/usuarios", headers=op, json=novo)).status_code == 403

    admin = await entrar(cliente, "admin", "senha-admin")
    for ruim in ({**novo, "senha": "123"}, {**novo, "perfil": "root"}, {**novo, "nome": "a b"}):
        assert (await cliente.post("/api/usuarios", headers=admin, json=ruim)).status_code == 422


# ----------------------------------------------------------------------
# Consultas
# ----------------------------------------------------------------------


async def test_historico_filtra_e_ordena_do_mais_novo(cliente, sessoes):
    g = Gravador(sessoes)
    await g.tratar(*status_online())
    for vaga, valor, ts in [
        (1, "livre", "2026-11-12T10:00:00Z"),
        (2, "ocupada", "2026-11-12T10:02:00Z"),
        (1, "ocupada", "2026-11-12T10:05:00Z"),
        (1, "livre", "2026-11-12T10:10:00Z"),
    ]:
        await g.tratar(*estado(vaga, valor, ts, 80.0 if valor == "livre" else 10.0))
    op = await entrar(cliente, "op", "senha-operador")

    async def historico(**params):
        r = await cliente.get("/api/historico/estados", headers=op, params=params)
        assert r.status_code == 200
        return [(e["vaga"], e["estado"]) for e in r.json()]

    assert await historico(vaga=1) == [(1, "livre"), (1, "ocupada"), (1, "livre")]
    assert await historico(vaga=1, desde="2026-11-12T10:04:00Z") == [(1, "livre"), (1, "ocupada")]
    assert await historico(ate="2026-11-12T10:03:00Z") == [(2, "ocupada"), (1, "livre")]
    assert await historico(limite=1) == [(1, "livre")]
    assert await historico(device_id="outro") == []
    r = await cliente.get("/api/historico/estados", headers=op, params={"limite": 0})
    assert r.status_code == 422


async def test_medicoes_por_grandeza(cliente, sessoes):
    await Gravador(sessoes).tratar(
        f"{BASE}/telemetria",
        carga(
            v=1, deviceId=DEVICE, ts="2026-11-12T10:00:00Z",
            vagas=[{"vaga": 1, "distancia": {"valor": 12.4, "unidade": "cm"}}],
            rssi={"valor": -61, "unidade": "dBm"}, uptime={"valor": 1000, "unidade": "ms"},
        ),
    )
    op = await entrar(cliente, "op", "senha-operador")
    rssi = (await cliente.get("/api/medicoes", headers=op, params={"grandeza": "rssi"})).json()
    assert [(x["valor"], x["unidade"], x["vaga"]) for x in rssi] == [(-61.0, "dBm", None)]
    dist = (await cliente.get("/api/medicoes", headers=op, params={"grandeza": "distancia", "vaga": 1})).json()
    assert [x["valor"] for x in dist] == [12.4]


async def test_rfid_dispositivos_e_alertas(cliente, sessoes):
    g = Gravador(sessoes)
    await g.tratar(*status_online())
    await g.tratar(*rfid(2, "04A1B2C3", "2026-11-12T10:00:07Z"))
    agora = datetime.now(timezone.utc)
    async with sessoes() as s, s.begin():
        s.add(m.Alerta(device_id=DEVICE, vaga=1, tipo="sensor_erro", mensagem="ativo", criado_em=agora))
        s.add(m.Alerta(device_id=DEVICE, vaga=2, tipo="sensor_erro", mensagem="resolvido",
                       criado_em=agora, resolvido_em=agora))
    op = await entrar(cliente, "op", "senha-operador")

    tags = (await cliente.get("/api/rfid", headers=op, params={"vaga": 2})).json()
    assert [(t["vaga"], t["uid"]) for t in tags] == [(2, "04A1B2C3")]
    disp = (await cliente.get("/api/dispositivos", headers=op)).json()
    assert [(d["device_id"], d["online"], d["firmware"]) for d in disp] == [(DEVICE, True, "0.10.0")]
    ativos = (await cliente.get("/api/alertas", headers=op)).json()
    assert [a["mensagem"] for a in ativos] == ["ativo"]
    todos = (await cliente.get("/api/alertas", headers=op, params={"ativos": False})).json()
    assert len(todos) == 2


async def test_vagas_mostram_offline(cliente, sessoes):
    await dispositivo_online(sessoes)
    await Gravador(sessoes).tratar(f"{BASE}/status", carga(v=1, deviceId=DEVICE, status="offline"))
    op = await entrar(cliente, "op", "senha-operador")
    vagas = (await cliente.get("/api/vagas", headers=op)).json()
    assert all(not v["online"] for v in vagas)


async def test_atuador_aparece_como_dispositivo_sem_vagas(cliente, sessoes):
    """ESP32 atuador (ADR 0008): status com "vagas": [] não cria cartões de vaga."""
    await dispositivo_online(sessoes)
    atuador = "esp32-atuador-f43b53000001"
    await Gravador(sessoes).tratar(
        f"garagem/v1/{atuador}/status",
        carga(v=1, deviceId=atuador, ts="2026-11-12T10:00:01Z", status="online", firmware="0.1.0", vagas=[]),
        retido=True,
    )
    op = await entrar(cliente, "op", "senha-operador")
    dispositivos = {d["device_id"]: d for d in (await cliente.get("/api/dispositivos", headers=op)).json()}
    assert dispositivos[atuador]["online"] and dispositivos[atuador]["vagas"] == []
    vagas = (await cliente.get("/api/vagas", headers=op)).json()
    assert {v["device_id"] for v in vagas} == {DEVICE}


async def test_vagas_mostram_leitor_rfid(cliente, sessoes):
    await dispositivo_online(sessoes)
    op = await entrar(cliente, "op", "senha-operador")
    # Sem telemetria com o campo: estado do leitor desconhecido
    vagas = (await cliente.get("/api/vagas", headers=op)).json()
    assert all(v["leitor_rfid"] is None for v in vagas)

    await Gravador(sessoes).tratar(
        f"{BASE}/telemetria",
        carga(
            v=1, deviceId=DEVICE, ts="2026-11-12T10:00:10Z",
            vagas=[{"vaga": 1, "distancia": None, "leitor_rfid": "ok"},
                   {"vaga": 2, "distancia": None, "leitor_rfid": "falha"}],
            rssi={"valor": -61, "unidade": "dBm"}, uptime={"valor": 10000, "unidade": "ms"},
        ),
    )
    vagas = {v["vaga"]: v for v in (await cliente.get("/api/vagas", headers=op)).json()}
    assert vagas[1]["leitor_rfid"] == "ok"
    assert vagas[2]["leitor_rfid"] == "falha" and vagas[2]["leitor_rfid_desde"].startswith("2026-11-12T10:00:10")


# ----------------------------------------------------------------------
# Comandos
# ----------------------------------------------------------------------

INVALIDOS = [
    {"acao": "reservar", "device_id": DEVICE, "vaga": 1, "duracao_min": 61},
    {"acao": "reservar", "device_id": DEVICE, "vaga": 1, "duracao_min": 0},
    {"acao": "reservar", "device_id": DEVICE, "vaga": 0},
    {"acao": "reservar", "device_id": DEVICE, "vaga": "um"},
    {"acao": "reservar", "vaga": 1},
    {"acao": "abrir_portao", "device_id": DEVICE, "vaga": 1},
    {"acao": "cancelar_reserva", "device_id": DEVICE},
    {"acao": "configurar", "device_id": DEVICE, "limiar_ocupa_cm": 10, "limiar_libera_cm": 401, "duracao_reserva_min": 15},
    {"acao": "configurar", "device_id": DEVICE, "limiar_ocupa_cm": 1, "limiar_libera_cm": 30, "duracao_reserva_min": 15},
    {"acao": "configurar", "device_id": DEVICE, "limiar_ocupa_cm": 10, "limiar_libera_cm": 30},
]


@pytest.mark.parametrize("corpo", INVALIDOS)
async def test_comando_invalido_nao_e_publicado(cliente, sessoes, publicador, corpo):
    await dispositivo_online(sessoes)
    admin = await entrar(cliente, "admin", "senha-admin")
    r = await cliente.post("/api/comandos", headers=admin, json=corpo)
    assert r.status_code == 422, r.text
    assert publicador.enviados == []
    async with sessoes() as s:
        assert await s.scalar(select(func.count()).select_from(m.Comando)) == 0


async def test_cada_comando_tem_command_id_proprio(cliente, sessoes, publicador):
    await dispositivo_online(sessoes)
    admin = await entrar(cliente, "admin", "senha-admin")
    corpo = {"acao": "cancelar_reserva", "device_id": DEVICE, "vaga": 1}
    ids = [(await cliente.post("/api/comandos", headers=admin, json=corpo)).json()["command_id"] for _ in range(3)]
    assert len(set(ids)) == 3
    lista = (await cliente.get("/api/comandos", headers=admin, params={"device_id": DEVICE})).json()
    assert {c["command_id"] for c in lista} == set(ids)
    assert (await cliente.get("/api/comandos/c-nao-existe", headers=admin)).status_code == 404


async def test_ciclo_completo_operador_e_persistencia(cliente, sessoes, publicador):
    """O operador envia; a persistência grava o andamento; o operador mostra confirmado."""
    g = Gravador(sessoes)
    await dispositivo_online(sessoes)
    admin = await entrar(cliente, "admin", "senha-admin")
    r = await cliente.post("/api/comandos", headers=admin, json={"acao": "reservar", "device_id": DEVICE, "vaga": 1})
    cid = r.json()["command_id"]

    # O que o firmware responderia, na ordem dele
    await g.tratar(*ack(cid, "aceito"))
    await g.tratar(*estado(1, "reservada", "2026-11-12T10:01:00Z", 80.0))
    await g.tratar(*ack(cid, "executado"))

    cmd = (await cliente.get(f"/api/comandos/{cid}", headers=admin)).json()
    assert cmd["status"] == "confirmado"
    assert cmd["recebido_em"] and cmd["executado_em"] and cmd["confirmado_em"]
    vaga1 = (await cliente.get("/api/vagas", headers=admin)).json()[0]
    assert vaga1["estado"] == "reservada"


async def test_rejeicao_chega_ao_painel(cliente, sessoes, publicador):
    g = Gravador(sessoes)
    await dispositivo_online(sessoes)
    admin = await entrar(cliente, "admin", "senha-admin")
    r = await cliente.post("/api/comandos", headers=admin, json={"acao": "reservar", "device_id": DEVICE, "vaga": 1})
    cid = r.json()["command_id"]
    await g.tratar(*ack(cid, "rejeitado", erro="VAGA_OCUPADA"))
    cmd = (await cliente.get(f"/api/comandos/{cid}", headers=admin)).json()
    assert (cmd["status"], cmd["erro"]) == ("rejeitado", "VAGA_OCUPADA")


class ClienteMqttFalso:
    def __init__(self):
        self.publicados = []

    async def publish(self, topico, payload, qos):
        self.publicados.append((topico, payload, qos))


async def test_publicacao_pelo_cliente_mqtt_do_servico(sessoes):
    """Sem publicador injetado: usa o cliente MQTT da conexão, com QoS 1."""
    import httpx

    agora = datetime.now(timezone.utc)
    async with sessoes() as s, s.begin():
        s.add(m.Usuario(nome="admin", senha_hash=auth.hash_senha("senha-admin"), perfil="administrador", criado_em=agora))
    await dispositivo_online(sessoes)
    app = criar_app(sessoes, jwt_segredo=SEGREDO, iniciar_mqtt=False)
    corpo = {"acao": "reservar", "device_id": DEVICE, "vaga": 1}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste") as c:
        admin = await entrar(c, "admin", "senha-admin")
        # Broker fora (operador ainda sem conexão)
        assert (await c.post("/api/comandos", headers=admin, json=corpo)).status_code == 503
        # Conectado
        app.state.cliente_mqtt = ClienteMqttFalso()
        assert (await c.post("/api/comandos", headers=admin, json=corpo)).status_code == 202
    [(topico, _, qos)] = app.state.cliente_mqtt.publicados
    assert (topico, qos) == (f"{BASE}/cmd", 1)


async def test_filtros_de_medicoes_e_rfid(cliente, sessoes):
    g = Gravador(sessoes)
    for ts in ("2026-11-12T10:00:00Z", "2026-11-12T11:00:00Z"):
        await g.tratar(
            f"{BASE}/telemetria",
            carga(v=1, deviceId=DEVICE, ts=ts, vagas=[], rssi={"valor": -61, "unidade": "dBm"},
                  uptime={"valor": 1000, "unidade": "ms"}),
        )
        await g.tratar(*rfid(1, "04A1B2C3", ts))
    op = await entrar(cliente, "op", "senha-operador")
    filtro = {"device_id": DEVICE, "desde": "2026-11-12T10:30:00Z"}
    assert len((await cliente.get("/api/medicoes", headers=op, params={**filtro, "ate": "2026-11-12T12:00:00Z"})).json()) == 1
    assert len((await cliente.get("/api/rfid", headers=op, params=filtro)).json()) == 1
    assert (await cliente.get("/api/rfid", headers=op, params={"device_id": "outro"})).json() == []


# ----------------------------------------------------------------------
# Tempo real (WebSocket) e saúde
# ----------------------------------------------------------------------


@pytest.fixture
def app_ws(monkeypatch):
    monkeypatch.delenv("ADMIN_USUARIO", raising=False)  # o ciclo de vida não toca no banco
    return criar_app(object(), jwt_segredo=SEGREDO, publicar=PublicadorFalso(), iniciar_mqtt=False)


def esperar_conexao(difusor, quantas=1):
    for _ in range(200):
        if difusor.conectados >= quantas:
            return
        time.sleep(0.01)
    raise AssertionError("WebSocket não se registrou no difusor")


def test_ws_sem_token_valido_e_fechado(app_ws):
    with TestClient(app_ws) as c:
        for url in ("/ws", "/ws?token=lixo"):
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(url) as ws:
                    ws.receive_json()


def test_ws_repassa_so_eventos_validos_do_dispositivo(app_ws):
    token = auth.criar_token(SEGREDO, "op", "operador")
    with TestClient(app_ws) as c, c.websocket_connect(f"/ws?token={token}") as ws:
        esperar_conexao(app_ws.state.difusor)
        receber = app_ws.state.ao_receber
        c.portal.call(receber, "garagem/vaga/1", b"livre", False)  # fora do contrato
        c.portal.call(
            receber, f"{BASE}/cmd",
            carga(v=1, commandId="c-1", ts="2026-11-12T10:00:00Z", acao="reservar", vaga=1), False,
        )  # comando: não volta para o painel
        c.portal.call(receber, *estado(1, "ocupada", "2026-11-12T10:00:00Z"), False)
        evento = ws.receive_json()
    assert evento["tipo"] == "estado" and evento["deviceId"] == DEVICE
    assert evento["dados"]["estado"] == "ocupada" and evento["retido"] is False


def test_ws_entrega_a_todos_os_paineis(app_ws):
    token = auth.criar_token(SEGREDO, "op", "operador")
    with TestClient(app_ws) as c:
        with c.websocket_connect(f"/ws?token={token}") as a, c.websocket_connect(f"/ws?token={token}") as b:
            esperar_conexao(app_ws.state.difusor, 2)
            c.portal.call(app_ws.state.ao_receber, *rfid(1, "04A1B2C3", "2026-11-12T10:00:07Z"), False)
            assert a.receive_json()["dados"]["uid"] == "04A1B2C3"
            assert b.receive_json()["dados"]["uid"] == "04A1B2C3"


def test_saude_informa_o_broker(app_ws):
    with TestClient(app_ws) as c:
        assert c.get("/saude").json() == {"servico": "operador", "broker": False}
