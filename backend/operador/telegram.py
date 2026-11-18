"""Notificação externa pelo Telegram (CP12, R15)."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

import httpx

from comum.config import opcional

log = logging.getLogger("telegram")

Notificar = Callable[[str], Awaitable[bool]]  # True se a mensagem foi entregue


class Telegram:
    """Envia mensagens por um bot (TELEGRAM_TOKEN) para um chat (TELEGRAM_CHAT_ID)."""

    def __init__(self, token: str, chat_id: str, cliente: Optional[httpx.AsyncClient] = None) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._cliente = cliente

    async def __call__(self, texto: str) -> bool:
        corpo = {"chat_id": self._chat_id, "text": texto}
        try:
            if self._cliente is not None:
                r = await self._cliente.post(self._url, json=corpo, timeout=10)
            else:
                async with httpx.AsyncClient() as cliente:
                    r = await cliente.post(self._url, json=corpo, timeout=10)
        except httpx.HTTPError as erro:
            # Sem a URL no log: ela contém o token do bot
            log.warning("Telegram indisponível (%s)", type(erro).__name__)
            return False
        if r.status_code != 200:
            log.warning("Telegram recusou a mensagem (HTTP %d)", r.status_code)
            return False
        return True


def telegram_do_ambiente() -> Optional[Telegram]:
    token, chat_id = opcional("TELEGRAM_TOKEN"), opcional("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.info("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID não definidos: alertas só no painel")
        return None
    return Telegram(token, chat_id)
