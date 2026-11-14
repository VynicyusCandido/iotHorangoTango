"""Simulador do ESP32 (J1): publica só o que o contrato aceita e responde como o firmware."""
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from comum import contrato
from persistencia.gravador import Gravador
from simulador.esp32 import SimuladorESP32

DEVICE = "esp32-garagem-simulado01"
T0 = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


class Relogio:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def avancar(self, **delta):
        self.t += timedelta(**delta)


@pytest.fixture
def relogio():
    return Relogio()


@pytest.fixture
def sim(relogio):
    return SimuladorESP32(DEVICE, agora=relogio, aleatorio=random.Random(1))


def validar(pubs):
    """Toda publicação do simulador precisa passar no contrato v1."""
    return [contrato.interpretar(p.topico, p.payload.encode()) for p in pubs]


def cmd(**campos):
    return json.dumps({"v": 1, "ts": "2026-11-14T12:00:00Z", **campos}).encode()


def acks(pubs):
    return [(m.dados.resultado, m.dados.erro) for m in validar(pubs) if m.tipo == "ack"]


def test_tudo_que_publica_segue_o_contrato(sim):
    pubs = [sim.status_online(), *sim.estados_atuais(), sim.telemetria()]
    pubs += sim.carro_chega(1, "04A1B2C3") + sim.falha_sensor(2) + sim.sensor_volta(2) + sim.carro_sai(1)
    pubs += sim.processar_comando(cmd(commandId="c-1", acao="reservar", vaga=1, duracao_min=1))
    for _ in range(200):
        pubs += sim.passo_aleatorio(chance=1.0)
    msgs = validar(pubs)
    assert {m.tipo for m in msgs} == {"status", "estado", "telemetria", "rfid", "ack"}
    retidos = {p.topico.rsplit("/", 1)[-1]: p.retain for p in pubs}
    assert retidos["status"] and retidos["estado"] and not retidos["rfid"] and not retidos["ack"]


def test_last_will_e_o_offline_do_contrato(sim):
    topico, payload = sim.lwt()
    m = contrato.interpretar(topico, payload.encode())
    assert m.dados.status == "offline" and m.dados.ts is None


def test_carro_com_tag_le_a_tag_antes_do_estado(sim):
    msgs = validar(sim.carro_chega(2, "0A0B0C0D"))
    assert [m.tipo for m in msgs] == ["rfid", "estado"]
    assert (msgs[1].dados.estado, msgs[1].dados.vaga) == ("ocupada", 2)
    assert sim.carro_chega(2) == []  # vaga já ocupada: nada muda


def test_reserva_na_ordem_do_firmware_e_expira(sim, relogio):
    pubs = sim.processar_comando(cmd(commandId="c-1", acao="reservar", vaga=1, duracao_min=2))
    msgs = validar(pubs)
    assert [(m.tipo, getattr(m.dados, "resultado", None) or m.dados.estado) for m in msgs] == [
        ("ack", "aceito"), ("estado", "reservada"), ("ack", "executado")
    ]
    relogio.avancar(minutes=1, seconds=59)
    assert sim.verificar_reservas() == []
    relogio.avancar(seconds=1)
    [expirou] = validar(sim.verificar_reservas())
    assert expirou.dados.estado == "livre"


def test_reserva_usa_a_duracao_configurada(sim, relogio):
    sim.processar_comando(cmd(commandId="c-1", acao="configurar", limiar_ocupa_cm=10, limiar_libera_cm=13,
                              duracao_reserva_min=3))
    sim.processar_comando(cmd(commandId="c-2", acao="reservar", vaga=1))
    relogio.avancar(minutes=2)
    assert sim.verificar_reservas() == []
    relogio.avancar(minutes=1)
    assert len(sim.verificar_reservas()) == 1


def test_carro_chegando_ou_sensor_falhando_encerra_a_reserva(sim):
    sim.processar_comando(cmd(commandId="c-1", acao="reservar", vaga=1))
    sim.processar_comando(cmd(commandId="c-2", acao="reservar", vaga=2))
    assert validar(sim.carro_chega(1))[-1].dados.estado == "ocupada"
    assert validar(sim.falha_sensor(2))[-1].dados.estado == "erro"
    assert sim.verificar_reservas() == []


def test_cancelar_reserva(sim):
    sim.processar_comando(cmd(commandId="c-1", acao="reservar", vaga=1))
    msgs = validar(sim.processar_comando(cmd(commandId="c-2", acao="cancelar_reserva", vaga=1)))
    assert [m.dados.estado for m in msgs if m.tipo == "estado"] == ["livre"]
    assert acks(sim.processar_comando(cmd(commandId="c-3", acao="cancelar_reserva", vaga=1))) == [
        ("rejeitado", "SEM_RESERVA")
    ]


@pytest.mark.parametrize(
    "payload,erro",
    [
        (b"nao e json", "PAYLOAD_INVALIDO"),
        (b"[1, 2]", "PAYLOAD_INVALIDO"),
        (cmd(acao="reservar", vaga=1), "PAYLOAD_INVALIDO"),  # sem commandId
        (cmd(commandId="x" * 41, acao="reservar", vaga=1), "PAYLOAD_INVALIDO"),
        (json.dumps({"v": 2, "commandId": "c-9", "acao": "reservar", "vaga": 1}).encode(), "PAYLOAD_INVALIDO"),
        (cmd(commandId="c-9", acao="abrir_portao"), "ACAO_DESCONHECIDA"),
        (cmd(commandId="c-9", acao="reservar"), "PARAMETRO_INVALIDO"),
        (cmd(commandId="c-9", acao="reservar", vaga="1"), "PARAMETRO_INVALIDO"),
        (cmd(commandId="c-9", acao="reservar", vaga=7), "VAGA_INEXISTENTE"),
        (cmd(commandId="c-9", acao="reservar", vaga=1, duracao_min=61), "PARAMETRO_INVALIDO"),
        (cmd(commandId="c-9", acao="reservar", vaga=1, duracao_min=2.0), "PARAMETRO_INVALIDO"),
        (cmd(commandId="c-9", acao="configurar", limiar_ocupa_cm=10, limiar_libera_cm=11, duracao_reserva_min=5),
         "PARAMETRO_INVALIDO"),
        (cmd(commandId="c-9", acao="configurar", limiar_ocupa_cm=10, limiar_libera_cm=30), "PARAMETRO_INVALIDO"),
    ],
)
def test_comando_invalido_rejeitado_com_o_codigo_do_firmware(sim, payload, erro):
    [m] = validar(sim.processar_comando(payload))
    assert (m.dados.resultado, m.dados.erro) == ("rejeitado", erro)


def test_intertravamento(sim):
    sim.carro_chega(1)
    sim.falha_sensor(2)
    assert acks(sim.processar_comando(cmd(commandId="c-1", acao="reservar", vaga=1))) == [("rejeitado", "VAGA_OCUPADA")]
    assert acks(sim.processar_comando(cmd(commandId="c-2", acao="reservar", vaga=2))) == [("rejeitado", "VAGA_EM_ERRO")]
    sim.carro_sai(1)
    sim.processar_comando(cmd(commandId="c-3", acao="reservar", vaga=1))
    # Já reservada: VAGA_OCUPADA (ADR 0004)
    assert acks(sim.processar_comando(cmd(commandId="c-4", acao="reservar", vaga=1))) == [("rejeitado", "VAGA_OCUPADA")]


def test_command_id_repetido_nao_reexecuta(sim):
    primeiro = sim.processar_comando(cmd(commandId="c-1", acao="reservar", vaga=1))
    assert len(primeiro) == 3
    assert acks(sim.processar_comando(cmd(commandId="c-1", acao="reservar", vaga=1))) == [
        ("rejeitado", "COMANDO_DUPLICADO")
    ]


def test_movimento_aleatorio_repetivel_com_semente(relogio):
    a = SimuladorESP32(DEVICE, agora=relogio, aleatorio=random.Random(42))
    b = SimuladorESP32(DEVICE, agora=relogio, aleatorio=random.Random(42))
    for _ in range(50):
        assert a.passo_aleatorio(0.5) == b.passo_aleatorio(0.5)


async def test_simulador_alimenta_a_persistencia(sessoes, sim):
    """Do simulador ao banco, sem broker: estados, tags e ciclo do comando."""
    from sqlalchemy import func, select

    from comum import modelos as m

    g = Gravador(sessoes)

    async def entregar(pubs):
        for p in pubs:
            await g.tratar(p.topico, p.payload.encode(), False)

    await entregar([sim.status_online(), *sim.estados_atuais(), sim.telemetria()])
    await entregar(sim.carro_chega(1, "04A1B2C3"))
    async with sessoes() as s:
        assert await s.scalar(select(func.count()).select_from(m.MensagemInvalida)) == 0
        assert await s.scalar(select(func.count()).select_from(m.LeituraRfid)) == 1
        estados = (await s.scalars(select(m.EstadoVaga.estado).where(m.EstadoVaga.vaga == 1)
                                   .order_by(m.EstadoVaga.id))).all()
    assert estados == ["livre", "ocupada"]
