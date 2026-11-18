"""Código usado pelos três assinantes: laço MQTT, difusor de tempo real e configuração."""
import asyncio
from contextlib import suppress

import aiomqtt
import pytest
from fastapi import WebSocketDisconnect

from comum import config, mqtt, tempo_real
from comum.tempo_real import Difusor


async def esperar(condicao, segundos=2.0):
    for _ in range(int(segundos / 0.01)):
        if condicao():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condição não aconteceu a tempo")


# ----------------------------------------------------------------------
# Laço MQTT (assinar_para_sempre)
# ----------------------------------------------------------------------


class MensagemFalsa:
    def __init__(self, topic, payload, retain=False):
        self.topic, self.payload, self.retain = topic, payload, retain


def broker_falso(roteiro):
    """Cliente aiomqtt falso. Cada conexão segue um passo do roteiro: uma
    exceção (falha ao conectar) ou a lista de mensagens que o broker entrega."""
    criados = []

    class ClienteFalso:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.assinados = []
            self._passo = roteiro[len(criados)]
            criados.append(self)

        async def __aenter__(self):
            if isinstance(self._passo, Exception):
                raise self._passo
            return self

        async def __aexit__(self, *erro):
            return False

        async def subscribe(self, topico, qos):
            self.assinados.append((topico, qos))

        @property
        def messages(self):
            return self._entregar()

        async def _entregar(self):
            for mensagem in self._passo:
                yield mensagem
            await asyncio.Event().wait()  # continua conectado

    return ClienteFalso, criados


@pytest.fixture
def ambiente_mqtt(monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "broker")
    monkeypatch.setenv("MQTT_PORT", "1884")
    monkeypatch.setenv("MQTT_USER", "backend")
    monkeypatch.setenv("MQTT_PASSWORD", "segredo")
    monkeypatch.setattr(mqtt, "INTERVALO_RECONEXAO_S", 0)
    return monkeypatch


async def test_reconecta_e_um_erro_no_tratador_nao_para_o_laco(ambiente_mqtt):
    Cliente, criados = broker_falso([
        aiomqtt.MqttError("broker fora"),
        [
            MensagemFalsa("a/1", b"um"),
            MensagemFalsa("a/2", bytearray(b"dois"), retain=True),
            MensagemFalsa("a/3", None),
            MensagemFalsa("a/4", "quatro"),
        ],
    ])
    ambiente_mqtt.setattr(mqtt.aiomqtt, "Client", Cliente)
    recebidas, conexoes = [], []

    async def tratar(topico, payload, retido):
        recebidas.append((topico, payload, retido))
        if topico == "a/2":
            raise ValueError("bug no tratador")

    async def ao_conectar(cliente):
        conexoes.append(cliente)

    tarefa = asyncio.create_task(mqtt.assinar_para_sempre("teste", ["t/#", "u/+"], tratar, ao_conectar))
    await esperar(lambda: len(recebidas) == 4)
    tarefa.cancel()
    with suppress(asyncio.CancelledError):
        await tarefa

    # Payload sempre chega como bytes; a mensagem depois do erro também chegou
    assert recebidas == [("a/1", b"um", False), ("a/2", b"dois", True), ("a/3", b"", False), ("a/4", b"quatro", False)]
    # Falhou, avisou desconexão, conectou, avisou o cliente, e ao parar avisou de novo
    assert conexoes == [None, criados[1], None]
    assert criados[1].assinados == [("t/#", 1), ("u/+", 1)]
    kw = criados[1].kwargs
    assert (kw["hostname"], kw["port"], kw["username"], kw["password"]) == ("broker", 1884, "backend", "segredo")
    # Mesmo client id nas reconexões, com sufixo para não derrubar outro processo
    assert kw["identifier"].startswith("teste-") and criados[0].kwargs["identifier"] == kw["identifier"]


async def test_sem_usuario_mqtt_nao_sobe(monkeypatch):
    monkeypatch.delenv("MQTT_USER", raising=False)
    monkeypatch.setenv("MQTT_PASSWORD", "x")
    with pytest.raises(RuntimeError, match="MQTT_USER"):
        await mqtt.assinar_para_sempre("teste", ["t/#"], None)


# ----------------------------------------------------------------------
# Difusor (WebSocket)
# ----------------------------------------------------------------------


class WebSocketFalso:
    def __init__(self, limite, porteira=None):
        self.enviados = []
        self.limite = limite
        self.porteira = porteira

    async def send_json(self, evento):
        if self.porteira:
            await self.porteira.wait()
        if len(self.enviados) >= self.limite:
            raise WebSocketDisconnect()  # navegador fechou
        self.enviados.append(evento)


async def test_difusor_entrega_em_ordem_e_esquece_quem_saiu():
    d = Difusor()
    ws = WebSocketFalso(limite=3)
    tarefa = asyncio.create_task(d.atender(ws, iniciais=[{"n": 0}]))
    await esperar(lambda: d.conectados == 1)
    for n in (1, 2, 3):
        d.enviar({"n": n})
    await asyncio.wait_for(tarefa, 1)
    assert ws.enviados == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert d.conectados == 0
    d.enviar({"n": 4})  # ninguém conectado: não falha


async def test_difusor_navegador_lento_perde_os_mais_antigos(monkeypatch):
    monkeypatch.setattr(tempo_real, "TAMANHO_FILA", 2)
    d = Difusor()
    porteira = asyncio.Event()
    ws = WebSocketFalso(limite=3, porteira=porteira)
    tarefa = asyncio.create_task(d.atender(ws, iniciais=[{"n": 0}]))
    await esperar(lambda: d.conectados == 1)
    for n in (1, 2, 3, 4):  # navegador travado: só cabem 2
        d.enviar({"n": n})
    porteira.set()
    await esperar(lambda: len(ws.enviados) == 3)
    d.enviar({"n": 5})  # o próximo envio encontra o navegador fechado
    await asyncio.wait_for(tarefa, 1)
    assert ws.enviados == [{"n": 0}, {"n": 3}, {"n": 4}]


async def test_um_navegador_lento_nao_atrasa_os_outros(monkeypatch):
    d = Difusor()
    travado = WebSocketFalso(limite=99, porteira=asyncio.Event())
    rapido = WebSocketFalso(limite=2)
    t1 = asyncio.create_task(d.atender(travado))
    t2 = asyncio.create_task(d.atender(rapido))
    await esperar(lambda: d.conectados == 2)
    d.enviar({"n": 1})
    d.enviar({"n": 2})
    d.enviar({"n": 3})
    await asyncio.wait_for(t2, 1)
    assert rapido.enviados == [{"n": 1}, {"n": 2}]
    t1.cancel()
    with suppress(asyncio.CancelledError):
        await t1


# ----------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------


def test_variavel_obrigatoria_ausente_explica_o_que_falta(monkeypatch):
    monkeypatch.delenv("JWT_SEGREDO", raising=False)
    with pytest.raises(RuntimeError, match=r"JWT_SEGREDO.*\.env\.example"):
        config.obrigatorio("JWT_SEGREDO")
    monkeypatch.setenv("JWT_SEGREDO", "")
    with pytest.raises(RuntimeError):
        config.obrigatorio("JWT_SEGREDO")  # vazio conta como ausente
    assert config.opcional("NAO_EXISTE", "padrao") == "padrao"
