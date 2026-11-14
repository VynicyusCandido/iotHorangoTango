"""Regras remotas (R8), alertas e Telegram (R15), com o simulador no lugar do ESP32."""
import json
import random
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from comum import modelos as m
from comum.tempo_real import Difusor
from operador.comandos import enviar_comando
from operador.regras import Vigia
from operador.telegram import Telegram
from persistencia.gravador import Gravador
from simulador.esp32 import SimuladorESP32
from tests.test_simulador import DEVICE, Relogio


class Mundo:
    """ESP32 simulado + persistência + vigia, ligados sem broker.

    O que o backend publica em cmd vai para o simulador, e as respostas
    dele vão para a persistência, como aconteceria pelo Mosquitto.
    """

    def __init__(self, sessoes):
        self.relogio = Relogio()
        self.sessoes = sessoes
        self.sim = SimuladorESP32(DEVICE, agora=self.relogio, aleatorio=random.Random(1))
        self.gravador = Gravador(sessoes, agora=self.relogio)
        self.publicados = []
        self.notificacoes = []
        self.telegram_ok = True
        self.difusor = Difusor()
        self.vigia = Vigia(sessoes, self.publicar, self.notificar, self.difusor, agora=self.relogio)

    async def publicar(self, topico, payload):
        self.publicados.append(json.loads(payload))
        await self.entregar(self.sim.processar_comando(payload.encode()))

    async def notificar(self, texto):
        if self.telegram_ok:
            self.notificacoes.append(texto)
        return self.telegram_ok

    async def entregar(self, pubs, retido=False):
        for p in pubs:
            await self.gravador.tratar(p.topico, p.payload.encode(), retido)

    async def ligar(self):
        await self.entregar([self.sim.status_online(), *self.sim.estados_atuais()])

    async def desligar(self):
        topico, payload = self.sim.lwt()
        await self.gravador.tratar(topico, payload.encode(), False)

    async def comando(self, acao, vaga=None, **parametros):
        async with self.sessoes() as s:
            cmd = await enviar_comando(
                s, self.publicar, device_id=DEVICE, acao=acao, vaga=vaga,
                parametros=parametros, origem="admin", agora=self.relogio(),
            )
        return cmd.command_id

    async def alertas(self, ativos=True):
        async with self.sessoes() as s:
            q = select(m.Alerta).order_by(m.Alerta.id)
            if ativos:
                q = q.where(m.Alerta.resolvido_em.is_(None))
            return (await s.scalars(q)).all()

    async def comandos(self):
        async with self.sessoes() as s:
            return (await s.scalars(select(m.Comando).order_by(m.Comando.id))).all()


@pytest.fixture
async def mundo(sessoes):
    w = Mundo(sessoes)
    await w.ligar()
    return w


# ----------------------------------------------------------------------
# Dispositivo offline
# ----------------------------------------------------------------------


async def test_offline_por_mais_de_1_min_gera_um_alerta_e_resolve_ao_voltar(mundo):
    await mundo.desligar()
    mundo.relogio.avancar(seconds=59)
    assert await mundo.vigia.verificar() == []
    mundo.relogio.avancar(seconds=2)
    [alerta] = await mundo.vigia.verificar()
    assert (alerta.tipo, alerta.vaga) == ("offline", None)
    assert len(mundo.notificacoes) == 1 and "offline" in mundo.notificacoes[0]

    mundo.relogio.avancar(minutes=5)
    assert await mundo.vigia.verificar() == []  # não repete
    assert len(mundo.notificacoes) == 1

    await mundo.ligar()
    await mundo.vigia.verificar()
    assert await mundo.alertas() == []
    [resolvido] = await mundo.alertas(ativos=False)
    assert resolvido.resolvido_em is not None


# ----------------------------------------------------------------------
# Sensor em erro
# ----------------------------------------------------------------------


async def test_sensor_em_erro_por_mais_de_1_min(mundo):
    await mundo.entregar(mundo.sim.falha_sensor(2))
    mundo.relogio.avancar(seconds=30)
    assert await mundo.vigia.verificar() == []
    mundo.relogio.avancar(seconds=31)
    [alerta] = await mundo.vigia.verificar()
    assert (alerta.tipo, alerta.vaga) == ("sensor_erro", 2)

    await mundo.entregar(mundo.sim.sensor_volta(2))
    await mundo.vigia.verificar()
    assert await mundo.alertas() == []


async def test_falha_curta_nao_alerta(mundo):
    await mundo.entregar(mundo.sim.falha_sensor(1))
    mundo.relogio.avancar(seconds=20)
    await mundo.entregar(mundo.sim.sensor_volta(1))
    mundo.relogio.avancar(minutes=2)
    assert await mundo.vigia.verificar() == []


# ----------------------------------------------------------------------
# Reserva vencida
# ----------------------------------------------------------------------


async def test_reserva_vencida_ainda_reservada_backend_cancela(mundo):
    """O ESP32 não expirou sozinho (ex.: relógio parado): o backend cancela."""
    cid = await mundo.comando("reservar", vaga=1, duracao_min=2)
    assert (await mundo.comandos())[0].status == "confirmado"

    mundo.relogio.avancar(minutes=2, seconds=20)  # passou o tempo, mas dentro da folga
    assert await mundo.vigia.verificar() == []
    mundo.relogio.avancar(seconds=15)
    [alerta] = await mundo.vigia.verificar()
    assert (alerta.tipo, alerta.vaga, alerta.chave) == ("reserva_expirada", 1, f"reserva:{cid}")

    reserva, cancelamento = await mundo.comandos()
    assert (cancelamento.acao, cancelamento.origem, cancelamento.vaga) == ("cancelar_reserva", "regra", 1)
    assert cancelamento.status == "confirmado"  # mesmo ciclo do R7, até a vaga voltar a livre
    assert mundo.publicados[-1]["acao"] == "cancelar_reserva"

    mundo.relogio.avancar(minutes=1)
    assert await mundo.vigia.verificar() == []  # tratada uma vez só
    assert len(await mundo.comandos()) == 2


async def test_reserva_que_o_esp32_expirou_so_gera_alerta(mundo):
    await mundo.comando("reservar", vaga=1, duracao_min=1)
    mundo.relogio.avancar(minutes=1)
    await mundo.entregar(mundo.sim.verificar_reservas())  # o ESP32 expira sozinho
    mundo.relogio.avancar(seconds=31)
    [alerta] = await mundo.vigia.verificar()
    assert alerta.tipo == "reserva_expirada"
    assert [c.acao for c in await mundo.comandos()] == ["reservar"]  # nada a cancelar


async def test_carro_chegou_nao_e_reserva_vencida(mundo):
    await mundo.comando("reservar", vaga=1, duracao_min=1)
    mundo.relogio.avancar(seconds=40)
    await mundo.entregar(mundo.sim.carro_chega(1, "04A1B2C3"))
    mundo.relogio.avancar(minutes=5)
    assert await mundo.vigia.verificar() == []


async def test_reserva_cancelada_pelo_operador_nao_alerta(mundo):
    await mundo.comando("reservar", vaga=1, duracao_min=1)
    mundo.relogio.avancar(seconds=10)
    await mundo.comando("cancelar_reserva", vaga=1)
    mundo.relogio.avancar(minutes=5)
    assert await mundo.vigia.verificar() == []


async def test_reserva_rejeitada_nao_conta(mundo):
    await mundo.entregar(mundo.sim.carro_chega(1))
    await mundo.comando("reservar", vaga=1, duracao_min=1)
    assert (await mundo.comandos())[0].status == "rejeitado"
    mundo.relogio.avancar(minutes=5)
    assert await mundo.vigia.verificar() == []


async def test_prazo_padrao_vem_do_banco(mundo):
    """Sem duracao_min, vale a duração configurada, salva no banco (R8)."""
    await mundo.comando("configurar", limiar_ocupa_cm=10.0, limiar_libera_cm=13.0, duracao_reserva_min=3)
    async with mundo.sessoes() as s:
        assert (await s.get(m.Dispositivo, DEVICE)).duracao_reserva_min == 3
    await mundo.comando("reservar", vaga=2)
    mundo.relogio.avancar(minutes=2, seconds=50)
    assert await mundo.vigia.verificar() == []
    mundo.relogio.avancar(minutes=1)
    [alerta] = await mundo.vigia.verificar()
    assert (alerta.tipo, alerta.vaga) == ("reserva_expirada", 2)


async def test_dispositivo_offline_adia_a_decisao_da_reserva(mundo):
    await mundo.comando("reservar", vaga=1, duracao_min=1)
    await mundo.desligar()
    mundo.relogio.avancar(minutes=3)
    tipos = [a.tipo for a in await mundo.vigia.verificar()]
    assert tipos == ["offline"]
    assert [c.acao for c in await mundo.comandos()] == ["reservar"]


# ----------------------------------------------------------------------
# Notificação
# ----------------------------------------------------------------------


async def test_telegram_fora_tenta_de_novo_no_proximo_ciclo(mundo):
    mundo.telegram_ok = False
    await mundo.desligar()
    mundo.relogio.avancar(minutes=2)
    await mundo.vigia.verificar()
    [alerta] = await mundo.alertas()
    assert alerta.notificado_em is None

    mundo.telegram_ok = True
    await mundo.vigia.verificar()
    [alerta] = await mundo.alertas()
    assert alerta.notificado_em is not None and len(mundo.notificacoes) == 1


async def test_alerta_novo_vai_ao_painel(mundo):
    recebidos = []
    mundo.difusor.enviar = recebidos.append
    await mundo.desligar()
    mundo.relogio.avancar(minutes=2)
    await mundo.vigia.verificar()
    assert recebidos[0]["tipo"] == "alerta" and recebidos[0]["dados"]["tipo"] == "offline"


async def test_sem_telegram_configurado_as_regras_funcionam(sessoes):
    w = Mundo(sessoes)
    w.vigia = Vigia(sessoes, w.publicar, notificar=None, agora=w.relogio)
    await w.ligar()
    await w.desligar()
    w.relogio.avancar(minutes=2)
    assert len(await w.vigia.verificar()) == 1


@pytest.mark.parametrize("resposta,esperado", [(200, True), (401, False), (None, False)])
async def test_cliente_telegram(resposta, esperado):
    pedidos = []

    def tratar(pedido: httpx.Request):
        pedidos.append(pedido)
        if resposta is None:
            raise httpx.ConnectError("sem internet")
        return httpx.Response(resposta, json={"ok": resposta == 200})

    async with httpx.AsyncClient(transport=httpx.MockTransport(tratar)) as cliente:
        telegram = Telegram("123:abc", "-100200", cliente)
        assert await telegram("sensor em falha") is esperado
    assert pedidos[0].url.path == "/bot123:abc/sendMessage"
    assert json.loads(pedidos[0].content) == {"chat_id": "-100200", "text": "sensor em falha"}


def test_telegram_so_liga_com_token_e_chat(monkeypatch):
    from operador.telegram import telegram_do_ambiente

    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert telegram_do_ambiente() is None
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    assert isinstance(telegram_do_ambiente(), Telegram)
