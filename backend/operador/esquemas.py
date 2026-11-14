"""Entradas e saídas da API do operador (aparecem no OpenAPI em /docs)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Perfil = Literal["operador", "administrador"]


class _Saida(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginReq(BaseModel):
    usuario: str
    senha: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    perfil: Perfil


class UsuarioOut(_Saida):
    nome: str
    perfil: Perfil


class UsuarioNovo(BaseModel):
    nome: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    senha: str = Field(min_length=8, max_length=72)  # bcrypt usa só 72 bytes
    perfil: Perfil


class DispositivoOut(_Saida):
    device_id: str
    firmware: Optional[str]
    vagas: Optional[list[int]]
    online: bool
    status_em: Optional[datetime]
    ultimo_contato: Optional[datetime]


class VagaOut(BaseModel):
    device_id: str
    vaga: int
    estado: Optional[str]  # null = ainda sem leitura gravada
    distancia_cm: Optional[float]
    desde: Optional[datetime]
    online: bool


class EstadoOut(_Saida):
    device_id: str
    vaga: int
    estado: str
    distancia_cm: Optional[float]
    ts: datetime
    ts_estimado: bool


class MedicaoOut(_Saida):
    device_id: str
    vaga: Optional[int]
    grandeza: str
    valor: float
    unidade: str
    ts: datetime


class RfidOut(_Saida):
    device_id: str
    vaga: int
    uid: str
    ts: datetime
    ts_estimado: bool


class AlertaOut(_Saida):
    id: int
    device_id: str
    vaga: Optional[int]
    tipo: str
    mensagem: str
    criado_em: datetime
    resolvido_em: Optional[datetime]


class ComandoOut(_Saida):
    command_id: str
    device_id: str
    acao: str
    vaga: Optional[int]
    parametros: dict[str, Any]
    origem: str
    status: str  # enviado, aceito, rejeitado, executado, confirmado ou falha_envio
    erro: Optional[str]
    enviado_em: datetime
    recebido_em: Optional[datetime]
    executado_em: Optional[datetime]
    confirmado_em: Optional[datetime]


# Comandos: mesmas faixas do CONTRATOS.md, checadas antes de publicar


class ReservarReq(BaseModel):
    acao: Literal["reservar"]
    device_id: str
    vaga: int = Field(ge=1)
    duracao_min: Optional[int] = Field(default=None, ge=1, le=60)


class CancelarReservaReq(BaseModel):
    acao: Literal["cancelar_reserva"]
    device_id: str
    vaga: int = Field(ge=1)


class ConfigurarReq(BaseModel):
    acao: Literal["configurar"]
    device_id: str
    limiar_ocupa_cm: float = Field(ge=2, le=200)
    limiar_libera_cm: float = Field(le=400)
    duracao_reserva_min: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def _histerese(self):
        if self.limiar_libera_cm <= self.limiar_ocupa_cm + 2:
            raise ValueError("limiar_libera_cm precisa ser maior que limiar_ocupa_cm + 2")
        return self


ComandoReq = Annotated[Union[ReservarReq, CancelarReservaReq, ConfigurarReq], Field(discriminator="acao")]
