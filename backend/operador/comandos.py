"""Registrar e publicar um comando (usado pela API e pelas regras remotas)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from comum import contrato, modelos as m

log = logging.getLogger("operador")

Publicar = Callable[[str, str], Awaitable[None]]  # (tópico, payload JSON)


class FalhaEnvio(Exception):
    """O broker não aceitou o comando; ele fica com status falha_envio."""


async def enviar_comando(
    s: AsyncSession,
    publicar: Publicar,
    *,
    device_id: str,
    acao: str,
    vaga: Optional[int],
    parametros: dict[str, Any],
    origem: str,
    agora: datetime,
) -> m.Comando:
    agora = agora.replace(microsecond=0)
    cmd = m.Comando(
        command_id=f"c-{uuid.uuid4().hex[:12]}",
        device_id=device_id,
        acao=acao,
        vaga=vaga,
        parametros=parametros,
        origem=origem,
        status="enviado",
        enviado_em=agora,
    )
    s.add(cmd)
    await s.commit()  # gravado antes de publicar: o ack pode chegar logo

    payload = contrato.Comando(commandId=cmd.command_id, ts=agora, acao=acao, vaga=vaga, **parametros)
    try:
        await publicar(contrato.topico_cmd(device_id), payload.model_dump_json(exclude_none=True))
    except Exception as erro:
        log.error("falha ao publicar %s: %s", cmd.command_id, erro)
        cmd.status = "falha_envio"
        await s.commit()
        raise FalhaEnvio(str(erro)) from None
    log.info("%s enviou %s (%s) para %s", origem, acao, cmd.command_id, device_id)
    return cmd
