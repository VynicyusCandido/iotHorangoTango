"""Simulador do ESP32: publica no broker como o firmware faria.

    python -m simulador --host 192.168.1.100 --usuario dispositivo --senha <senha>
    docker compose --profile simulador up -d     (usa o usuário "dispositivo" do .env)

Conecta com o deviceId como client id (a ACL exige), registra o Last
Will, publica status e estado das vagas, telemetria a cada 10 s, carros
chegando e saindo ao acaso e responde aos comandos do backend.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random

import aiomqtt

from comum.config import configurar_log, opcional
from simulador.esp32 import Publicacao, SimuladorESP32

log = logging.getLogger("simulador")

INTERVALO_TELEMETRIA_S = 10
INTERVALO_RECONEXAO_S = 5


def argumentos() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m simulador", description=__doc__.splitlines()[0])
    p.add_argument("--host", default=opcional("MQTT_HOST", "localhost"))
    p.add_argument("--porta", type=int, default=int(opcional("MQTT_PORT", "1883")))
    p.add_argument("--usuario", default=opcional("MQTT_USER"), help="padrão: MQTT_USER")
    p.add_argument("--senha", default=opcional("MQTT_PASSWORD"), help="padrão: MQTT_PASSWORD")
    p.add_argument("--device-id", default="esp32-garagem-simulado01")
    p.add_argument("--vagas", type=int, nargs="+", default=[1, 2], help="números das vagas (padrão: 1 2)")
    p.add_argument("--tags", default="04A1B2C3,0A0B0C0D,DEADBEEF", help="UIDs dos carros com tag")
    p.add_argument("--intervalo", type=float, default=5.0, help="segundos entre movimentos ao acaso")
    p.add_argument("--chance", type=float, default=0.3, help="chance de um carro chegar/sair a cada intervalo")
    p.add_argument("--semente", type=int, default=None, help="semente do sorteio (repetir a mesma sequência)")
    return p.parse_args()


async def rodar(args: argparse.Namespace) -> None:
    sim = SimuladorESP32(
        args.device_id,
        tuple(args.vagas),
        tuple(t.strip().upper() for t in args.tags.split(",") if t.strip()),
        aleatorio=random.Random(args.semente),
    )
    topico_lwt, payload_lwt = sim.lwt()
    log.info("deviceId: %s, vagas %s", sim.device_id, list(sim.vagas))

    while True:
        try:
            async with aiomqtt.Client(
                hostname=args.host,
                port=args.porta,
                username=args.usuario or None,
                password=args.senha or None,
                identifier=sim.device_id,
                keepalive=10,
                clean_session=True,
                will=aiomqtt.Will(topico_lwt, payload_lwt, qos=1, retain=True),
            ) as cliente:

                async def publicar(lista: list[Publicacao]) -> None:
                    for p in lista:
                        await cliente.publish(p.topico, p.payload, qos=p.qos, retain=p.retain)
                        if not p.topico.endswith("/telemetria"):
                            log.info("%s %s", p.topico.removeprefix(sim.base + "/"), p.payload)

                await cliente.subscribe(sim.topico_cmd, qos=1)
                log.info("conectado a %s:%d", args.host, args.porta)
                await publicar([sim.status_online(), *sim.estados_atuais()])

                async def comandos() -> None:
                    async for msg in cliente.messages:
                        payload = msg.payload if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload).encode()
                        await publicar(sim.processar_comando(bytes(payload)))

                async def relogio() -> None:
                    segundos = 0
                    while True:
                        await asyncio.sleep(1)
                        segundos += 1
                        await publicar(sim.verificar_reservas())
                        if segundos % max(1, round(args.intervalo)) == 0:
                            await publicar(sim.passo_aleatorio(args.chance))
                        if segundos % INTERVALO_TELEMETRIA_S == 0:
                            await publicar([sim.telemetria()])

                await asyncio.gather(comandos(), relogio())
        except aiomqtt.MqttError as erro:
            log.warning("sem broker (%s); nova tentativa em %d s", erro, INTERVALO_RECONEXAO_S)
            await asyncio.sleep(INTERVALO_RECONEXAO_S)


def main() -> None:
    configurar_log()
    try:
        asyncio.run(rodar(argumentos()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
