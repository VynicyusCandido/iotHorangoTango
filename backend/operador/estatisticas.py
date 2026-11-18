"""Indicadores para os gráficos do painel (CP12, R14), calculados do histórico.

A partir das mudanças de estado (estados_vaga) e de online/offline
(eventos_status), monta os intervalos em que cada estado valeu dentro do
período pedido e calcula:

- ocupação por hora do dia: fração do tempo observado em `ocupada`;
- tempo médio de permanência: duração média dos intervalos `ocupada`
  que começaram e terminaram dentro do período;
- tempo em `erro` por vaga;
- uptime do dispositivo: fração do tempo `online`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterable, Optional


@dataclass(frozen=True)
class Intervalo:
    inicio: datetime
    fim: datetime
    estado: str
    completo: bool  # começou e terminou por uma mudança dentro do período

    @property
    def segundos(self) -> float:
        return (self.fim - self.inicio).total_seconds()


def intervalos(
    eventos: Iterable[tuple[datetime, str]],
    inicial: Optional[str],
    desde: datetime,
    ate: datetime,
) -> list[Intervalo]:
    """`eventos` em ordem, com ts dentro de (desde, ate]; `inicial` é o
    estado que valia em `desde` (None se desconhecido)."""
    saida: list[Intervalo] = []
    atual, inicio, aberto_no_inicio = inicial, desde, True
    for ts, estado in eventos:
        if ts <= desde:
            atual = estado
            continue
        if ts > ate:
            break
        if atual is not None and ts > inicio:
            saida.append(Intervalo(inicio, ts, atual, completo=not aberto_no_inicio))
        atual, inicio, aberto_no_inicio = estado, ts, False
    if atual is not None and ate > inicio:
        saida.append(Intervalo(inicio, ate, atual, completo=False))
    return saida


def ocupacao_por_hora(lista: Iterable[Intervalo], fuso: tzinfo) -> list[Optional[float]]:
    """24 valores (0 a 1) por hora local do dia; None se a hora não foi observada.
    Tempo em `erro` não conta como observado (não se sabe se havia carro)."""
    ocupado = [0.0] * 24
    observado = [0.0] * 24
    for iv in lista:
        if iv.estado == "erro":
            continue
        atual = iv.inicio
        while atual < iv.fim:
            local = atual.astimezone(fuso)
            virada = (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc)
            fim = min(iv.fim, virada)
            segundos = (fim - atual).total_seconds()
            observado[local.hour] += segundos
            if iv.estado == "ocupada":
                ocupado[local.hour] += segundos
            atual = fim
    return [round(o / t, 4) if t > 0 else None for o, t in zip(ocupado, observado)]


def permanencia_media_min(lista: Iterable[Intervalo]) -> tuple[Optional[float], int]:
    """(média em minutos, quantidade) dos intervalos `ocupada` completos."""
    duracoes = [iv.segundos for iv in lista if iv.estado == "ocupada" and iv.completo]
    if not duracoes:
        return None, 0
    return round(sum(duracoes) / len(duracoes) / 60, 2), len(duracoes)


def minutos_em(lista: Iterable[Intervalo], estado: str) -> float:
    return round(sum(iv.segundos for iv in lista if iv.estado == estado) / 60, 2)


def fracao_em(lista: Iterable[Intervalo], estado: str) -> Optional[float]:
    lista = list(lista)
    total = sum(iv.segundos for iv in lista)
    if total <= 0:
        return None
    return round(sum(iv.segundos for iv in lista if iv.estado == estado) / total, 4)
