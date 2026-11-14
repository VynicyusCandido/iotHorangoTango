"""Laço de assinatura MQTT com reconexão, comum aos três assinantes."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Optional

import aiomqtt

from .config import obrigatorio, opcional

log = logging.getLogger("mqtt")

Tratador = Callable[[str, bytes, bool], Awaitable[None]]  # (tópico, payload, retido)
AoConectar = Callable[[Optional[aiomqtt.Client]], Awaitable[None]]  # None = desconectou

INTERVALO_RECONEXAO_S = 5


def _payload(bruto) -> bytes:
    if isinstance(bruto, (bytes, bytearray)):
        return bytes(bruto)
    if bruto is None:
        return b""
    return str(bruto).encode()


async def assinar_para_sempre(
    nome: str,
    topicos: list[str],
    tratar: Tratador,
    ao_conectar: Optional[AoConectar] = None,
) -> None:
    """Conecta ao broker, assina os tópicos e entrega cada mensagem a `tratar`.

    Reconecta a cada 5 s se o broker cair. Um erro ao tratar uma mensagem é
    registrado e não derruba o laço.
    """
    host = opcional("MQTT_HOST", "localhost")
    porta = int(opcional("MQTT_PORT", "1883"))
    usuario = obrigatorio("MQTT_USER")
    senha = obrigatorio("MQTT_PASSWORD")
    # Sufixo aleatório: dois processos do mesmo serviço não se derrubam
    identificador = f"{nome}-{uuid.uuid4().hex[:8]}"

    while True:
        try:
            async with aiomqtt.Client(
                hostname=host,
                port=porta,
                username=usuario,
                password=senha,
                identifier=identificador,
                keepalive=30,
            ) as cliente:
                for topico in topicos:
                    await cliente.subscribe(topico, qos=1)
                log.info("%s conectado a %s:%d, assinando %s", nome, host, porta, ", ".join(topicos))
                if ao_conectar:
                    await ao_conectar(cliente)
                async for msg in cliente.messages:
                    try:
                        await tratar(str(msg.topic), _payload(msg.payload), bool(msg.retain))
                    except Exception:
                        log.exception("erro ao tratar mensagem de %s", msg.topic)
        except aiomqtt.MqttError as erro:
            log.warning("%s sem broker (%s); nova tentativa em %d s", nome, erro, INTERVALO_RECONEXAO_S)
        finally:
            if ao_conectar:
                await ao_conectar(None)
        await asyncio.sleep(INTERVALO_RECONEXAO_S)
