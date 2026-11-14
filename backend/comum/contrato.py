"""Contrato MQTT v1 (CONTRATOS.md): tópicos e payloads.

Os três assinantes do backend interpretam o que chega do broker com
`interpretar()`. Mensagem fora do contrato levanta `ErroContrato`, cujo
texto é o motivo (gravado em `mensagens_invalidas` pela persistência).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

VERSAO = 1

_RE_TOPICO = re.compile(
    r"^garagem/v1/(?P<device>[A-Za-z0-9_-]{1,64})/"
    r"(?:(?P<tipo>telemetria|rfid|status|ack|cmd)|vaga/(?P<vaga>[0-9]{1,3})/estado)$"
)

CodigoErro = Literal[
    "VAGA_OCUPADA",
    "VAGA_EM_ERRO",
    "VAGA_INEXISTENTE",
    "SEM_RESERVA",
    "PARAMETRO_INVALIDO",
    "ACAO_DESCONHECIDA",
    "COMANDO_DUPLICADO",
    "PAYLOAD_INVALIDO",
]
CommandId = Annotated[str, StringConstraints(min_length=1, max_length=40)]
NumeroVaga = Annotated[int, Field(ge=1)]


class ErroContrato(ValueError):
    """Mensagem fora do contrato v1; str(erro) é o motivo."""


class _Modelo(BaseModel):
    # strict: "12.4" não vira número, 1.0 não vira vaga 1; campo a mais é erro
    model_config = ConfigDict(extra="forbid", strict=True)


class Grandeza(_Modelo):
    valor: float
    unidade: str


class Distancia(Grandeza):
    valor: float = Field(ge=0)
    unidade: Literal["cm"]


class Rssi(Grandeza):
    unidade: Literal["dBm"]


class Uptime(Grandeza):
    valor: float = Field(ge=0)
    unidade: Literal["ms"]


class _DoDispositivo(_Modelo):
    """Cabeçalho de tudo o que o ESP32 publica."""

    v: Literal[1]
    deviceId: str
    ts: Optional[datetime] = None
    ts_sincronizado: Optional[bool] = None
    uptime_ms: Optional[int] = Field(default=None, ge=0)

    @field_validator("ts")
    @classmethod
    def _ts_com_fuso(cls, ts: Optional[datetime]) -> Optional[datetime]:
        if ts is not None and ts.utcoffset() is None:
            raise ValueError("ts precisa de fuso (ISO 8601 UTC, ex.: 2026-10-27T21:30:00Z)")
        return ts

    def _exigir_momento(self) -> None:
        if self.ts is None and (self.uptime_ms is None or self.ts_sincronizado is not False):
            raise ValueError('"ts": null exige "ts_sincronizado": false e "uptime_ms"')

    @model_validator(mode="after")
    def _momento(self):
        self._exigir_momento()
        return self


class TelemetriaVaga(_Modelo):
    vaga: NumeroVaga
    distancia: Optional[Distancia]


class Telemetria(_DoDispositivo):
    vagas: list[TelemetriaVaga]
    rssi: Rssi
    uptime: Uptime


class EstadoVaga(_DoDispositivo):
    vaga: NumeroVaga
    estado: Literal["livre", "ocupada", "reservada", "erro"]
    distancia: Optional[Distancia]

    @model_validator(mode="after")
    def _erro_sem_distancia(self):
        if self.estado == "erro" and self.distancia is not None:
            raise ValueError('estado "erro" vai com "distancia": null')
        return self


class Rfid(_DoDispositivo):
    vaga: NumeroVaga
    # 4, 7 ou 10 bytes, hexadecimal maiúsculo sem separadores
    uid: Annotated[str, StringConstraints(pattern=r"^(?:[0-9A-F]{2}){4,10}$")]


class Ack(_DoDispositivo):
    commandId: Optional[CommandId]  # null quando o comando não tinha commandId legível
    resultado: Literal["aceito", "rejeitado", "executado"]
    erro: Optional[CodigoErro] = None

    @model_validator(mode="after")
    def _erro_so_no_rejeitado(self):
        if (self.resultado == "rejeitado") != (self.erro is not None):
            raise ValueError('"erro" vai só (e sempre) com "resultado": "rejeitado"')
        return self


class Status(_DoDispositivo):
    status: Literal["online", "offline"]
    firmware: Optional[str] = None
    vagas: Optional[list[NumeroVaga]] = None

    def _exigir_momento(self) -> None:
        # O Last Will (offline) é registrado na conexão e vai sem ts
        if self.status == "online":
            super()._exigir_momento()

    @model_validator(mode="after")
    def _online_completo(self):
        if self.status == "online" and (self.firmware is None or self.vagas is None):
            raise ValueError('status "online" exige "firmware" e "vagas"')
        return self


class Comando(_Modelo):
    """Payload de garagem/v1/{id}/cmd (só o backend publica)."""

    v: Literal[1] = 1
    commandId: CommandId
    ts: datetime
    acao: Literal["reservar", "cancelar_reserva", "configurar"]
    vaga: Optional[NumeroVaga] = None
    duracao_min: Optional[int] = Field(default=None, ge=1, le=60)
    limiar_ocupa_cm: Optional[float] = None
    limiar_libera_cm: Optional[float] = None
    duracao_reserva_min: Optional[int] = None


_MODELOS: dict[str, type[_Modelo]] = {
    "telemetria": Telemetria,
    "estado": EstadoVaga,
    "rfid": Rfid,
    "ack": Ack,
    "status": Status,
    "cmd": Comando,
}


@dataclass(frozen=True)
class Mensagem:
    device_id: str
    tipo: str  # telemetria, estado, rfid, ack, status ou cmd
    dados: _Modelo


def _resumir(erro: ValidationError) -> str:
    partes = []
    for e in erro.errors()[:5]:
        local = ".".join(str(p) for p in e["loc"]) or "payload"
        partes.append(f"{local}: {e['msg']}")
    return "; ".join(partes)


def interpretar(topico: str, payload: bytes) -> Mensagem:
    """Valida tópico e payload contra o contrato v1."""
    m = _RE_TOPICO.match(topico)
    if m is None:
        raise ErroContrato(f"tópico fora do contrato v1: {topico}")
    device_id = m["device"]
    tipo = m["tipo"] or "estado"

    try:
        dados = _MODELOS[tipo].model_validate_json(payload)
    except ValidationError as erro:
        raise ErroContrato(_resumir(erro)) from None

    if tipo != "cmd" and dados.deviceId != device_id:
        raise ErroContrato(f"deviceId do payload ({dados.deviceId}) difere do tópico ({device_id})")
    if tipo == "estado" and dados.vaga != int(m["vaga"]):
        raise ErroContrato(f"vaga do payload ({dados.vaga}) difere do tópico ({m['vaga']})")
    return Mensagem(device_id, tipo, dados)


def topico_cmd(device_id: str) -> str:
    return f"garagem/v{VERSAO}/{device_id}/cmd"
