"""python -m persistencia"""
import asyncio

from comum.config import configurar_log
from comum.db import criar_sessoes
from comum.mqtt import assinar_para_sempre
from persistencia.gravador import Gravador


async def main() -> None:
    configurar_log()
    gravador = Gravador(criar_sessoes())
    await assinar_para_sempre("persistencia", ["garagem/v1/#"], gravador.tratar)


if __name__ == "__main__":
    asyncio.run(main())
