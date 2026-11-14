"""Grava as mensagens do contrato v1 no banco e acompanha o ciclo dos comandos."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from comum import contrato, modelos as m
from comum.db import inserir_ignorando

log = logging.getLogger("persistencia")

TAMANHO_MAX_INVALIDA = 4000


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _naive(ts: datetime) -> datetime:
    """Compara horários vindos do banco (o SQLite dos testes perde o fuso)."""
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


class Relogios:
    """Estima o horário de eventos que chegam com "ts": null (ESP32 sem NTP).

    A telemetria é publicada na hora (não passa pela fila do firmware), então
    o par (horário, uptime) dela serve de referência: o evento aconteceu
    `uptime_ref - uptime_evento` ms antes da referência (ADR 0003, J2).
    """

    def __init__(self) -> None:
        self._ref: dict[str, tuple[datetime, int]] = {}

    def registrar(self, device_id: str, quando: datetime, uptime_ms: int) -> None:
        self._ref[device_id] = (quando, uptime_ms)

    def estimar(self, device_id: str, uptime_ms: int, recebido_em: datetime) -> datetime:
        ref = self._ref.get(device_id)
        if ref is None:
            return recebido_em
        quando, uptime_ref = ref
        estimado = quando - timedelta(milliseconds=uptime_ref - uptime_ms)
        # Referência de outro boot daria horário no futuro: fica o recebimento
        return min(estimado, recebido_em)


class Gravador:
    def __init__(
        self,
        sessoes: async_sessionmaker[AsyncSession],
        relogios: Optional[Relogios] = None,
        agora: Callable[[], datetime] = agora_utc,
    ) -> None:
        self._sessoes = sessoes
        self._relogios = relogios or Relogios()
        self._agora = agora

    async def tratar(self, topico: str, payload: bytes, retido: bool = False) -> None:
        recebido = self._agora()
        try:
            msg = contrato.interpretar(topico, payload)
        except contrato.ErroContrato as erro:
            log.warning("mensagem inválida em %s: %s", topico, erro)
            await self._gravar_invalida(topico, payload, str(erro), recebido)
            return
        if msg.tipo == "cmd":
            return  # publicado pelo operador, que já registrou o comando

        async with self._sessoes() as s, s.begin():
            disp = await self._dispositivo(s, msg.device_id)
            if not retido:  # retido é mensagem antiga, não é contato
                disp.ultimo_contato = recebido
            tratar_tipo = getattr(self, f"_{msg.tipo}")
            await tratar_tipo(s, disp, msg.dados, recebido, retido)

    # ------------------------------------------------------------------

    async def _gravar_invalida(self, topico: str, payload: bytes, motivo: str, recebido: datetime) -> None:
        async with self._sessoes() as s, s.begin():
            s.add(
                m.MensagemInvalida(
                    topico=topico[:500],
                    payload=payload.decode("utf-8", errors="replace")[:TAMANHO_MAX_INVALIDA],
                    motivo=motivo,
                    recebido_em=recebido,
                )
            )

    async def _dispositivo(self, s: AsyncSession, device_id: str) -> m.Dispositivo:
        disp = await s.get(m.Dispositivo, device_id)
        if disp is None:
            disp = m.Dispositivo(device_id=device_id, online=False)
            s.add(disp)
            await s.flush()
            log.info("novo dispositivo: %s", device_id)
        return disp

    def _quando(self, device_id: str, dados, recebido: datetime) -> tuple[datetime, bool]:
        """(ts do evento, ts_estimado)."""
        if dados.ts is not None:
            return dados.ts, False
        return self._relogios.estimar(device_id, dados.uptime_ms, recebido), True

    # ------------------------------------------------------------------

    async def _telemetria(self, s, disp, t: contrato.Telemetria, recebido, retido) -> None:
        self._relogios.registrar(disp.device_id, t.ts or recebido, int(t.uptime.valor))
        ts, estimado = self._quando(disp.device_id, t, recebido)
        comum = dict(device_id=disp.device_id, ts=ts, ts_estimado=estimado, recebido_em=recebido)
        for v in t.vagas:
            if v.distancia is not None:
                s.add(m.Medicao(vaga=v.vaga, grandeza="distancia", valor=v.distancia.valor, unidade="cm", **comum))
        s.add(m.Medicao(vaga=None, grandeza="rssi", valor=t.rssi.valor, unidade="dBm", **comum))

    async def _estado(self, s, disp, e: contrato.EstadoVaga, recebido, retido) -> None:
        ts, estimado = self._quando(disp.device_id, e, recebido)
        # Ao reconectar, o firmware republica o estado atual com ts novo;
        # repetir o último estado da vaga não é mudança e não vira linha
        anterior = await s.scalar(
            select(m.EstadoVaga)
            .where(m.EstadoVaga.device_id == disp.device_id, m.EstadoVaga.vaga == e.vaga)
            .order_by(m.EstadoVaga.ts.desc(), m.EstadoVaga.id.desc())
            .limit(1)
        )
        if anterior is not None and anterior.estado == e.estado and _naive(anterior.ts) <= _naive(ts):
            return
        novo = await inserir_ignorando(
            s,
            m.EstadoVaga,
            dict(
                device_id=disp.device_id,
                vaga=e.vaga,
                estado=e.estado,
                distancia_cm=e.distancia.valor if e.distancia else None,
                ts=ts,
                ts_estimado=estimado,
                recebido_em=recebido,
            ),
        )
        if novo and not retido:
            await self._confirmar_por_estado(s, disp.device_id, e.vaga, e.estado, recebido)

    async def _confirmar_por_estado(self, s, device_id: str, vaga: int, estado: str, recebido) -> None:
        """Último passo do R7: o estado publicado confirma o comando.

        O firmware publica aceito -> estado -> executado; o estado chega antes
        do executado, então aqui só se marca confirmado_em, e o status vira
        "confirmado" quando o executado chegar (ou já, se ele chegou antes).
        """
        acao = "reservar" if estado == "reservada" else "cancelar_reserva"
        cmd = await s.scalar(
            select(m.Comando)
            .where(
                m.Comando.device_id == device_id,
                m.Comando.vaga == vaga,
                m.Comando.acao == acao,
                m.Comando.status.in_(["aceito", "executado"]),
                m.Comando.confirmado_em.is_(None),
            )
            .order_by(m.Comando.enviado_em.desc())
            .limit(1)
        )
        if cmd is None:
            return
        cmd.confirmado_em = recebido
        if cmd.status == "executado":
            cmd.status = "confirmado"

    async def _rfid(self, s, disp, r: contrato.Rfid, recebido, retido) -> None:
        ts, estimado = self._quando(disp.device_id, r, recebido)
        await inserir_ignorando(
            s,
            m.LeituraRfid,
            dict(device_id=disp.device_id, vaga=r.vaga, uid=r.uid, ts=ts, ts_estimado=estimado, recebido_em=recebido),
        )

    async def _ack(self, s, disp, a: contrato.Ack, recebido, retido) -> None:
        if a.commandId is None:
            log.warning("%s rejeitou um comando sem commandId legível (%s)", disp.device_id, a.erro)
            return
        cmd = await s.scalar(select(m.Comando).where(m.Comando.command_id == a.commandId))
        if cmd is None or cmd.device_id != disp.device_id:
            log.warning("ack de comando desconhecido: %s (%s)", a.commandId, disp.device_id)
            return

        cmd.recebido_em = cmd.recebido_em or recebido
        if a.resultado == "rejeitado":
            # Reentrega do mesmo cmd (QoS 1) volta como COMANDO_DUPLICADO:
            # não apaga o resultado do comando original
            if a.erro == "COMANDO_DUPLICADO" and cmd.status != "enviado":
                return
            cmd.status = "rejeitado"
            cmd.erro = a.erro
        elif a.resultado == "aceito":
            if cmd.status == "enviado":
                cmd.status = "aceito"
        else:  # executado
            cmd.executado_em = cmd.executado_em or recebido
            if cmd.status in ("enviado", "aceito"):
                cmd.status = "confirmado" if cmd.confirmado_em else "executado"

    async def _status(self, s, disp, st: contrato.Status, recebido, retido) -> None:
        ts = st.ts or recebido
        disp.online = st.status == "online"
        disp.status_em = ts
        if st.firmware is not None:
            disp.firmware = st.firmware
        if st.vagas is not None:
            disp.vagas = st.vagas
        if not retido:
            s.add(m.EventoStatus(device_id=disp.device_id, status=st.status, ts=ts, recebido_em=recebido))
