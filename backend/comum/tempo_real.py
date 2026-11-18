"""Difusão de eventos para navegadores conectados por WebSocket."""
from __future__ import annotations

import asyncio
from typing import Any, Iterable

from fastapi import WebSocket, WebSocketDisconnect

TAMANHO_FILA = 100


class Difusor:
    def __init__(self) -> None:
        self._filas: set[asyncio.Queue] = set()

    @property
    def conectados(self) -> int:
        return len(self._filas)

    def enviar(self, evento: dict[str, Any]) -> None:
        for fila in list(self._filas):
            if fila.full():  # navegador lento: perde o evento mais antigo
                fila.get_nowait()
            fila.put_nowait(evento)

    async def atender(self, ws: WebSocket, iniciais: Iterable[dict[str, Any]] = ()) -> None:
        """Mantém o WebSocket aberto, enviando os eventos até o navegador sair."""
        fila: asyncio.Queue = asyncio.Queue(maxsize=TAMANHO_FILA)
        self._filas.add(fila)
        try:
            for evento in iniciais:
                await ws.send_json(evento)
            while True:
                await ws.send_json(await fila.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._filas.discard(fila)
