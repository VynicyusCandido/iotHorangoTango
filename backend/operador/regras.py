"""Regras remotas (CP12, R8) e alertas (R15). Rodam no operador a cada 15 s.

- Sensor em `erro` há mais de 1 min -> alerta (resolvido quando a vaga sai de erro).
- Dispositivo `offline` há mais de 1 min -> alerta (resolvido quando volta).
- Reserva vencida sem o carro chegar -> alerta; se a vaga ainda estiver
  `reservada`, o backend envia `cancelar_reserva` (mesmo ciclo do R7).
  O prazo é o `duracao_min` do comando ou a duração salva no banco.

Cada alerta novo vai ao painel (WebSocket) e ao Telegram, se configurado.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from comum import modelos as m
from comum.horario import agora_utc, utc
from comum.tempo_real import Difusor
from operador.comandos import FalhaEnvio, Publicar, enviar_comando
from operador.telegram import Notificar

log = logging.getLogger("regras")

PRAZO_ERRO = timedelta(minutes=1)
PRAZO_OFFLINE = timedelta(minutes=1)
# Folga para o ESP32 expirar a reserva sozinho antes de o backend agir
MARGEM_RESERVA = timedelta(seconds=30)
# Reservas mais antigas que isso não são mais verificadas
JANELA_RESERVAS = timedelta(days=1)
INTERVALO_S = 15
ORIGEM = "regra"


class Vigia:
    def __init__(
        self,
        sessoes: async_sessionmaker[AsyncSession],
        publicar: Publicar,
        notificar: Optional[Notificar] = None,
        difusor: Optional[Difusor] = None,
        agora: Callable[[], datetime] = agora_utc,
    ) -> None:
        self._sessoes = sessoes
        self._publicar = publicar
        self._notificar = notificar
        self._difusor = difusor
        self._agora = agora

    async def rodar_para_sempre(self) -> None:
        while True:
            try:
                await self.verificar()
            except Exception:
                log.exception("erro ao aplicar as regras")
            await asyncio.sleep(INTERVALO_S)

    async def verificar(self) -> list[m.Alerta]:
        """Aplica as regras uma vez; devolve os alertas criados."""
        agora = self._agora()
        novos: list[m.Alerta] = []
        cancelar: list[tuple[str, int]] = []

        async with self._sessoes() as s, s.begin():
            dispositivos = (await s.scalars(select(m.Dispositivo))).all()
            for d in dispositivos:
                await self._offline(s, d, agora, novos)
                for vaga in d.vagas or []:
                    await self._sensor_erro(s, d, vaga, agora, novos)
            await self._reservas(s, {d.device_id: d for d in dispositivos}, agora, novos, cancelar)

        for device_id, vaga in cancelar:
            async with self._sessoes() as s:
                try:
                    await enviar_comando(
                        s, self._publicar, device_id=device_id, acao="cancelar_reserva", vaga=vaga,
                        parametros={}, origem=ORIGEM, agora=agora,
                    )
                except FalhaEnvio:
                    pass  # fica registrado como falha_envio; o alerta já avisa

        for alerta in novos:
            log.warning("alerta: %s", alerta.mensagem)
            if self._difusor is not None:
                self._difusor.enviar({"tipo": "alerta", "dados": _alerta_json(alerta)})
        await self._notificar_pendentes()
        return novos

    # ------------------------------------------------------------------

    async def _abrir(self, s, novos, *, chave, tipo, device_id, vaga, mensagem, agora) -> None:
        if await s.scalar(select(m.Alerta.id).where(m.Alerta.chave == chave, m.Alerta.resolvido_em.is_(None))):
            return
        alerta = m.Alerta(
            device_id=device_id, vaga=vaga, tipo=tipo, chave=chave, mensagem=mensagem, criado_em=agora
        )
        s.add(alerta)
        novos.append(alerta)

    async def _resolver(self, s, chave: str, agora: datetime) -> None:
        for alerta in (
            await s.scalars(select(m.Alerta).where(m.Alerta.chave == chave, m.Alerta.resolvido_em.is_(None)))
        ).all():
            alerta.resolvido_em = agora

    async def _offline(self, s, d: m.Dispositivo, agora, novos) -> None:
        chave = f"offline:{d.device_id}"
        if d.online:
            await self._resolver(s, chave, agora)
        elif d.status_em is not None and agora - utc(d.status_em) >= PRAZO_OFFLINE:
            await self._abrir(
                s, novos, chave=chave, tipo="offline", device_id=d.device_id, vaga=None, agora=agora,
                mensagem=f"Dispositivo {d.device_id} offline desde {_hora(d.status_em)}",
            )

    async def _sensor_erro(self, s, d: m.Dispositivo, vaga: int, agora, novos) -> None:
        chave = f"erro:{d.device_id}:{vaga}"
        ultimo = await _ultimo_estado(s, d.device_id, vaga)
        if ultimo is None:
            return
        if ultimo.estado != "erro":
            await self._resolver(s, chave, agora)
        elif agora - utc(ultimo.recebido_em) >= PRAZO_ERRO:
            await self._abrir(
                s, novos, chave=chave, tipo="sensor_erro", device_id=d.device_id, vaga=vaga, agora=agora,
                mensagem=f"Sensor da vaga {vaga} ({d.device_id}) em falha desde {_hora(ultimo.ts)}",
            )

    async def _reservas(self, s, dispositivos, agora, novos, cancelar) -> None:
        reservas = (
            await s.scalars(
                select(m.Comando).where(
                    m.Comando.acao == "reservar",
                    m.Comando.status.in_(["executado", "confirmado"]),
                    m.Comando.enviado_em >= agora - JANELA_RESERVAS,
                )
            )
        ).all()
        for cmd in reservas:
            chave = f"reserva:{cmd.command_id}"
            if await s.scalar(select(m.Alerta.id).where(m.Alerta.chave == chave)):
                continue  # já tratada
            d = dispositivos.get(cmd.device_id)
            if d is None or not d.online:
                continue  # o alerta de offline cobre; decide quando voltar
            inicio = utc(cmd.executado_em or cmd.recebido_em or cmd.enviado_em)
            duracao = int((cmd.parametros or {}).get("duracao_min") or d.duracao_reserva_min)
            if agora < inicio + timedelta(minutes=duracao) + MARGEM_RESERVA:
                continue

            # O carro chegou? (horário do servidor nos dois lados)
            chegou = await s.scalar(
                select(m.EstadoVaga.id).where(
                    m.EstadoVaga.device_id == cmd.device_id,
                    m.EstadoVaga.vaga == cmd.vaga,
                    m.EstadoVaga.estado == "ocupada",
                    m.EstadoVaga.recebido_em >= inicio,
                )
            )
            if chegou:
                continue
            # Alguém cancelou antes?
            cancelada = await s.scalar(
                select(m.Comando.id).where(
                    m.Comando.device_id == cmd.device_id,
                    m.Comando.vaga == cmd.vaga,
                    m.Comando.acao == "cancelar_reserva",
                    m.Comando.enviado_em >= cmd.enviado_em,
                    m.Comando.status.notin_(["rejeitado", "falha_envio"]),
                )
            )
            if cancelada:
                continue
            ultimo = await _ultimo_estado(s, cmd.device_id, cmd.vaga)
            if ultimo is None or ultimo.estado not in ("reservada", "livre"):
                continue  # em erro: a reserva acabou pela falha, não pelo tempo
            await self._abrir(
                s, novos, chave=chave, tipo="reserva_expirada", device_id=cmd.device_id, vaga=cmd.vaga,
                agora=agora, mensagem=f"Reserva da vaga {cmd.vaga} ({cmd.device_id}) expirou sem o carro chegar",
            )
            if ultimo.estado == "reservada":
                cancelar.append((cmd.device_id, cmd.vaga))

    async def _notificar_pendentes(self) -> None:
        """Telegram: envia os alertas ativos ainda não notificados (tenta de novo se falhou)."""
        if self._notificar is None:
            return
        async with self._sessoes() as s, s.begin():
            pendentes = (
                await s.scalars(
                    select(m.Alerta)
                    .where(m.Alerta.notificado_em.is_(None), m.Alerta.resolvido_em.is_(None))
                    .order_by(m.Alerta.criado_em)
                )
            ).all()
            for alerta in pendentes:
                if await self._notificar(f"⚠️ HorangoTango Park: {alerta.mensagem}"):
                    alerta.notificado_em = self._agora()
                else:
                    break  # Telegram fora: tenta de novo no próximo ciclo


async def _ultimo_estado(s, device_id: str, vaga: int) -> Optional[m.EstadoVaga]:
    return await s.scalar(
        select(m.EstadoVaga)
        .where(m.EstadoVaga.device_id == device_id, m.EstadoVaga.vaga == vaga)
        .order_by(m.EstadoVaga.ts.desc(), m.EstadoVaga.id.desc())
        .limit(1)
    )


def _hora(ts: datetime) -> str:
    return utc(ts).strftime("%d/%m %H:%M UTC")


def _alerta_json(a: m.Alerta) -> dict:
    return {
        "device_id": a.device_id,
        "vaga": a.vaga,
        "tipo": a.tipo,
        "mensagem": a.mensagem,
        "criado_em": utc(a.criado_em).isoformat(),
    }
