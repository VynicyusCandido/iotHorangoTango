"""Horários sempre em UTC com fuso."""
from __future__ import annotations

from datetime import datetime, timezone


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc(ts: datetime) -> datetime:
    """Horário vindo do banco com fuso UTC (o SQLite dos testes perde o fuso)."""
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
