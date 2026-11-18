"""Tabelas do PostgreSQL (CP12, R5). Mudou aqui: crie uma migração em migracoes/versions."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Momento = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class Dispositivo(Base):
    __tablename__ = "dispositivos"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    firmware: Mapped[Optional[str]] = mapped_column(String(32))
    vagas: Mapped[Optional[list[int]]] = mapped_column(JSON)
    online: Mapped[bool] = mapped_column(default=False)
    status_em: Mapped[Optional[datetime]] = mapped_column(Momento)        # último online/offline
    ultimo_contato: Mapped[Optional[datetime]] = mapped_column(Momento)   # última mensagem recebida ao vivo
    # Duração padrão da reserva no ESP32 (comando configurar), salva no
    # banco para a regra de reserva vencida (R8)
    duracao_reserva_min: Mapped[int] = mapped_column(default=15, server_default="15")


class EventoStatus(Base):
    """Histórico de online/offline (uptime do dispositivo, R14)."""

    __tablename__ = "eventos_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("dispositivos.device_id"))
    status: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(Momento)
    recebido_em: Mapped[datetime] = mapped_column(Momento)


class Medicao(Base):
    __tablename__ = "medicoes"
    __table_args__ = (Index("ix_medicoes_busca", "device_id", "grandeza", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("dispositivos.device_id"))
    vaga: Mapped[Optional[int]] = mapped_column(Integer)  # null = do dispositivo (ex.: RSSI)
    grandeza: Mapped[str] = mapped_column(String(32))
    valor: Mapped[float] = mapped_column(Float)
    unidade: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(Momento)
    ts_estimado: Mapped[bool] = mapped_column(default=False)
    recebido_em: Mapped[datetime] = mapped_column(Momento)


# Mesma mensagem recebida de novo (estado retido reentregue, republicação
# após PUBACK perdido) não vira linha nova. Só vale para ts real.
_SO_TS_REAL = {"postgresql_where": text("NOT ts_estimado"), "sqlite_where": text("NOT ts_estimado")}


class EstadoVaga(Base):
    __tablename__ = "estados_vaga"
    __table_args__ = (
        # Com o estado: duas mudanças no mesmo segundo (ts tem precisão de
        # segundos) são eventos diferentes; repetição tem o mesmo estado
        Index("ux_estados_vaga_evento", "device_id", "vaga", "ts", "estado", unique=True, **_SO_TS_REAL),
        Index("ix_estados_vaga_busca", "device_id", "vaga", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("dispositivos.device_id"))
    vaga: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(16))
    distancia_cm: Mapped[Optional[float]] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(Momento)
    ts_estimado: Mapped[bool] = mapped_column(default=False)
    recebido_em: Mapped[datetime] = mapped_column(Momento)


class LeituraRfid(Base):
    __tablename__ = "leituras_rfid"
    __table_args__ = (
        Index("ux_leituras_rfid_evento", "device_id", "vaga", "uid", "ts", unique=True, **_SO_TS_REAL),
        Index("ix_leituras_rfid_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("dispositivos.device_id"))
    vaga: Mapped[int] = mapped_column(Integer)
    uid: Mapped[str] = mapped_column(String(20))
    ts: Mapped[datetime] = mapped_column(Momento)
    ts_estimado: Mapped[bool] = mapped_column(default=False)
    recebido_em: Mapped[datetime] = mapped_column(Momento)


class Comando(Base):
    """Ciclo do R7: enviado -> aceito/rejeitado -> executado -> confirmado."""

    __tablename__ = "comandos"

    id: Mapped[int] = mapped_column(primary_key=True)
    command_id: Mapped[str] = mapped_column(String(40), unique=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("dispositivos.device_id"))
    acao: Mapped[str] = mapped_column(String(32))
    vaga: Mapped[Optional[int]] = mapped_column(Integer)
    parametros: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    origem: Mapped[str] = mapped_column(String(64))  # usuário que enviou
    status: Mapped[str] = mapped_column(String(16))
    erro: Mapped[Optional[str]] = mapped_column(String(32))
    enviado_em: Mapped[datetime] = mapped_column(Momento)
    recebido_em: Mapped[Optional[datetime]] = mapped_column(Momento)
    executado_em: Mapped[Optional[datetime]] = mapped_column(Momento)
    confirmado_em: Mapped[Optional[datetime]] = mapped_column(Momento)


class Alerta(Base):
    """Gerado pelas regras remotas (R8): sensor_erro, offline, reserva_expirada."""

    __tablename__ = "alertas"
    __table_args__ = (Index("ix_alertas_chave", "chave"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("dispositivos.device_id"))
    vaga: Mapped[Optional[int]] = mapped_column(Integer)
    tipo: Mapped[str] = mapped_column(String(32))
    # Identifica a situação (ex.: "erro:<device>:1"): no máximo um alerta
    # ativo por chave, e a regra o resolve quando a situação acaba
    chave: Mapped[str] = mapped_column(String(128), default="", server_default="")
    mensagem: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(Momento)
    resolvido_em: Mapped[Optional[datetime]] = mapped_column(Momento)
    notificado_em: Mapped[Optional[datetime]] = mapped_column(Momento)  # Telegram (R15)


class Tag(Base):
    """Tags RFID cadastradas pelo administrador (R16). Privilégios: ADR futuro."""

    __tablename__ = "tags"

    uid: Mapped[str] = mapped_column(String(20), primary_key=True)
    apelido: Mapped[str] = mapped_column(String(64))
    dono: Mapped[Optional[str]] = mapped_column(String(64))
    criada_em: Mapped[datetime] = mapped_column(Momento)


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(64), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(100))
    perfil: Mapped[str] = mapped_column(String(16))  # operador ou administrador
    criado_em: Mapped[datetime] = mapped_column(Momento)


class MensagemInvalida(Base):
    __tablename__ = "mensagens_invalidas"

    id: Mapped[int] = mapped_column(primary_key=True)
    topico: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)
    motivo: Mapped[str] = mapped_column(Text)
    recebido_em: Mapped[datetime] = mapped_column(Momento)
