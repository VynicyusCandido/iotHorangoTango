"""API do operador e do administrador (painel web).

uvicorn operador.app:app — documentação OpenAPI em /docs.

Assina garagem/v1/# para repassar ao painel, em tempo real (WebSocket
/ws), tudo o que o dispositivo publica. É o único serviço que publica
comandos (garagem/v1/{id}/cmd). Estado, histórico e andamento dos
comandos vêm do banco, gravado pelo assinante de persistência.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Annotated, AsyncIterator, Awaitable, Callable, Optional

import jwt
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from comum import contrato, modelos as m
from comum.config import configurar_log, obrigatorio, opcional
from comum.db import criar_sessoes
from comum.mqtt import assinar_para_sempre
from comum.tempo_real import Difusor
from operador import auth
from operador.esquemas import (
    AlertaOut,
    ComandoOut,
    ComandoReq,
    DispositivoOut,
    EstadoOut,
    LoginReq,
    MedicaoOut,
    RfidOut,
    TokenOut,
    UsuarioNovo,
    UsuarioOut,
    VagaOut,
)

log = logging.getLogger("operador")

Publicar = Callable[[str, str], Awaitable[None]]  # (tópico, payload JSON)

# Compara a senha mesmo com usuário inexistente: a resposta leva o mesmo
# tempo e não revela quais usuários existem
_HASH_FALSO = auth.hash_senha("usuario-inexistente")


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------
# Dependências
# ----------------------------------------------------------------------


async def _sessao(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessoes() as s:
        yield s


Sessao = Annotated[AsyncSession, Depends(_sessao)]
_bearer = HTTPBearer(auto_error=False)


async def _usuario_atual(
    request: Request,
    cred: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> UsuarioOut:
    nao_autorizado = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "faça login (POST /api/login)", headers={"WWW-Authenticate": "Bearer"}
    )
    if cred is None:
        raise nao_autorizado
    try:
        dados = auth.ler_token(request.app.state.jwt_segredo, cred.credentials)
    except jwt.PyJWTError:
        raise nao_autorizado from None
    return UsuarioOut(nome=dados["sub"], perfil=dados.get("perfil", "operador"))


Logado = Annotated[UsuarioOut, Depends(_usuario_atual)]


def _exigir_admin(usuario: Logado) -> UsuarioOut:
    if usuario.perfil != "administrador":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "só o administrador pode fazer isso")
    return usuario


Admin = Annotated[UsuarioOut, Depends(_exigir_admin)]
Limite = Annotated[int, Query(ge=1, le=1000)]

rotas = APIRouter()

# ----------------------------------------------------------------------
# Login e usuários (R9, R16)
# ----------------------------------------------------------------------


@rotas.post("/api/login", response_model=TokenOut, tags=["login"])
async def login(req: LoginReq, request: Request, s: Sessao):
    u = await s.scalar(select(m.Usuario).where(m.Usuario.nome == req.usuario))
    if not auth.senha_confere(req.senha, u.senha_hash if u else _HASH_FALSO) or u is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuário ou senha incorretos")
    token = auth.criar_token(request.app.state.jwt_segredo, u.nome, u.perfil)
    return TokenOut(access_token=token, perfil=u.perfil)


@rotas.get("/api/eu", response_model=UsuarioOut, tags=["login"])
async def eu(usuario: Logado):
    return usuario


@rotas.post("/api/usuarios", response_model=UsuarioOut, status_code=201, tags=["login"])
async def criar_usuario(novo: UsuarioNovo, _: Admin, s: Sessao):
    if await s.scalar(select(m.Usuario).where(m.Usuario.nome == novo.nome)):
        raise HTTPException(status.HTTP_409_CONFLICT, "usuário já existe")
    u = m.Usuario(nome=novo.nome, senha_hash=auth.hash_senha(novo.senha), perfil=novo.perfil, criado_em=_agora())
    s.add(u)
    await s.commit()
    return u


# ----------------------------------------------------------------------
# Consulta (operador e administrador)
# ----------------------------------------------------------------------


@rotas.get("/api/dispositivos", response_model=list[DispositivoOut], tags=["consulta"])
async def dispositivos(_: Logado, s: Sessao):
    return (await s.scalars(select(m.Dispositivo).order_by(m.Dispositivo.device_id))).all()


@rotas.get("/api/vagas", response_model=list[VagaOut], tags=["consulta"])
async def vagas(_: Logado, s: Sessao):
    """Estado atual de cada vaga (último estado gravado) e se o dispositivo está online."""
    saida = []
    for d in (await s.scalars(select(m.Dispositivo).order_by(m.Dispositivo.device_id))).all():
        for numero in d.vagas or []:
            e = await s.scalar(
                select(m.EstadoVaga)
                .where(m.EstadoVaga.device_id == d.device_id, m.EstadoVaga.vaga == numero)
                .order_by(m.EstadoVaga.ts.desc(), m.EstadoVaga.id.desc())
                .limit(1)
            )
            saida.append(
                VagaOut(
                    device_id=d.device_id,
                    vaga=numero,
                    estado=e.estado if e else None,
                    distancia_cm=e.distancia_cm if e else None,
                    desde=e.ts if e else None,
                    online=d.online,
                )
            )
    return saida


@rotas.get("/api/historico/estados", response_model=list[EstadoOut], tags=["consulta"])
async def historico_estados(
    _: Logado,
    s: Sessao,
    device_id: Optional[str] = None,
    vaga: Optional[int] = None,
    desde: Optional[datetime] = None,
    ate: Optional[datetime] = None,
    limite: Limite = 200,
):
    q = select(m.EstadoVaga)
    if device_id:
        q = q.where(m.EstadoVaga.device_id == device_id)
    if vaga is not None:
        q = q.where(m.EstadoVaga.vaga == vaga)
    if desde:
        q = q.where(m.EstadoVaga.ts >= desde)
    if ate:
        q = q.where(m.EstadoVaga.ts <= ate)
    return (await s.scalars(q.order_by(m.EstadoVaga.ts.desc()).limit(limite))).all()


@rotas.get("/api/medicoes", response_model=list[MedicaoOut], tags=["consulta"])
async def medicoes(
    _: Logado,
    s: Sessao,
    device_id: Optional[str] = None,
    grandeza: Optional[str] = Query(default=None, description="distancia ou rssi"),
    vaga: Optional[int] = None,
    desde: Optional[datetime] = None,
    ate: Optional[datetime] = None,
    limite: Limite = 200,
):
    q = select(m.Medicao)
    if device_id:
        q = q.where(m.Medicao.device_id == device_id)
    if grandeza:
        q = q.where(m.Medicao.grandeza == grandeza)
    if vaga is not None:
        q = q.where(m.Medicao.vaga == vaga)
    if desde:
        q = q.where(m.Medicao.ts >= desde)
    if ate:
        q = q.where(m.Medicao.ts <= ate)
    return (await s.scalars(q.order_by(m.Medicao.ts.desc()).limit(limite))).all()


@rotas.get("/api/rfid", response_model=list[RfidOut], tags=["consulta"])
async def leituras_rfid(
    _: Logado,
    s: Sessao,
    device_id: Optional[str] = None,
    vaga: Optional[int] = None,
    desde: Optional[datetime] = None,
    limite: Limite = 100,
):
    q = select(m.LeituraRfid)
    if device_id:
        q = q.where(m.LeituraRfid.device_id == device_id)
    if vaga is not None:
        q = q.where(m.LeituraRfid.vaga == vaga)
    if desde:
        q = q.where(m.LeituraRfid.ts >= desde)
    return (await s.scalars(q.order_by(m.LeituraRfid.ts.desc()).limit(limite))).all()


@rotas.get("/api/alertas", response_model=list[AlertaOut], tags=["consulta"])
async def alertas(_: Logado, s: Sessao, ativos: bool = True, limite: Limite = 100):
    q = select(m.Alerta)
    if ativos:
        q = q.where(m.Alerta.resolvido_em.is_(None))
    return (await s.scalars(q.order_by(m.Alerta.criado_em.desc()).limit(limite))).all()


# ----------------------------------------------------------------------
# Comandos (R7): só o administrador envia
# ----------------------------------------------------------------------


@rotas.get("/api/comandos", response_model=list[ComandoOut], tags=["comandos"])
async def listar_comandos(_: Logado, s: Sessao, device_id: Optional[str] = None, limite: Limite = 50):
    q = select(m.Comando)
    if device_id:
        q = q.where(m.Comando.device_id == device_id)
    return (await s.scalars(q.order_by(m.Comando.enviado_em.desc()).limit(limite))).all()


@rotas.get("/api/comandos/{command_id}", response_model=ComandoOut, tags=["comandos"])
async def ver_comando(command_id: str, _: Logado, s: Sessao):
    cmd = await s.scalar(select(m.Comando).where(m.Comando.command_id == command_id))
    if cmd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "comando não encontrado")
    return cmd


@rotas.post("/api/comandos", response_model=ComandoOut, status_code=202, tags=["comandos"])
async def enviar_comando(req: Annotated[ComandoReq, Body()], usuario: Admin, request: Request, s: Sessao):
    """Publica o comando e devolve o commandId; acompanhe em GET /api/comandos/{commandId}.

    Ciclo: enviado -> aceito (ou rejeitado, com o erro) -> executado -> confirmado.
    """
    disp = await s.get(m.Dispositivo, req.device_id)
    if disp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dispositivo desconhecido")
    if not disp.online:
        # Sessão limpa no ESP32: comando publicado agora se perderia
        raise HTTPException(status.HTTP_409_CONFLICT, "dispositivo offline; comando não seria entregue")
    vaga = getattr(req, "vaga", None)
    if vaga is not None and disp.vagas and vaga not in disp.vagas:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"o dispositivo não tem a vaga {vaga}")

    agora = _agora().replace(microsecond=0)
    parametros = req.model_dump(exclude={"acao", "device_id", "vaga"}, exclude_none=True)
    cmd = m.Comando(
        command_id=f"c-{uuid.uuid4().hex[:12]}",
        device_id=disp.device_id,
        acao=req.acao,
        vaga=vaga,
        parametros=parametros,
        origem=usuario.nome,
        status="enviado",
        enviado_em=agora,
    )
    s.add(cmd)
    await s.commit()  # gravado antes de publicar: o ack pode chegar logo

    payload = contrato.Comando(commandId=cmd.command_id, ts=agora, acao=req.acao, vaga=vaga, **parametros)
    try:
        await request.app.state.publicar(contrato.topico_cmd(disp.device_id), payload.model_dump_json(exclude_none=True))
    except Exception as erro:
        log.error("falha ao publicar %s: %s", cmd.command_id, erro)
        cmd.status = "falha_envio"
        await s.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "broker indisponível; comando não enviado") from None
    log.info("%s enviou %s (%s) para %s", usuario.nome, req.acao, cmd.command_id, disp.device_id)
    return cmd


# ----------------------------------------------------------------------
# Tempo real e saúde
# ----------------------------------------------------------------------


@rotas.websocket("/ws")
async def tempo_real(websocket: WebSocket, token: str = ""):
    """Eventos do dispositivo ao vivo. Conecte em /ws?token=<access_token>."""
    try:
        auth.ler_token(websocket.app.state.jwt_segredo, token)
    except jwt.PyJWTError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    await websocket.app.state.difusor.atender(websocket)


@rotas.get("/saude", tags=["saúde"])
async def saude(request: Request):
    return {"servico": "operador", "broker": request.app.state.cliente_mqtt is not None}


# ----------------------------------------------------------------------


async def _criar_admin_inicial(sessoes: async_sessionmaker[AsyncSession]) -> None:
    """Cria o administrador do .env (ADMIN_USUARIO / ADMIN_SENHA) se ainda não existir."""
    nome, senha = opcional("ADMIN_USUARIO"), opcional("ADMIN_SENHA")
    if not nome or not senha:
        return
    async with sessoes() as s, s.begin():
        if await s.scalar(select(m.Usuario).where(m.Usuario.nome == nome)) is None:
            s.add(m.Usuario(nome=nome, senha_hash=auth.hash_senha(senha), perfil="administrador", criado_em=_agora()))
            log.info("administrador inicial criado: %s", nome)


def criar_app(
    sessoes: Optional[async_sessionmaker[AsyncSession]] = None,
    *,
    jwt_segredo: Optional[str] = None,
    publicar: Optional[Publicar] = None,
    iniciar_mqtt: bool = True,
) -> FastAPI:
    """Sem argumentos, lê tudo do ambiente ao subir (produção). Os testes
    passam sessões, segredo e um `publicar` falso e não sobem o MQTT."""

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        configurar_log()
        if app.state.sessoes is None:
            app.state.sessoes = criar_sessoes()
        if app.state.jwt_segredo is None:
            app.state.jwt_segredo = obrigatorio("JWT_SEGREDO")
        await _criar_admin_inicial(app.state.sessoes)
        tarefa = None
        if iniciar_mqtt:
            tarefa = asyncio.create_task(
                assinar_para_sempre("operador", ["garagem/v1/#"], ao_receber, ao_conectar)
            )
        yield
        if tarefa:
            tarefa.cancel()
            with suppress(asyncio.CancelledError):
                await tarefa

    app = FastAPI(title="HorangoTango Park — operador", version="1.0.0", lifespan=ciclo)
    app.state.sessoes = sessoes
    app.state.jwt_segredo = jwt_segredo
    app.state.cliente_mqtt = None
    app.state.difusor = Difusor()

    async def publicar_mqtt(topico: str, payload: str) -> None:
        cliente = app.state.cliente_mqtt
        if cliente is None:
            raise RuntimeError("sem conexão com o broker")
        await cliente.publish(topico, payload, qos=1)

    app.state.publicar = publicar or publicar_mqtt

    async def ao_conectar(cliente) -> None:
        app.state.cliente_mqtt = cliente

    async def ao_receber(topico: str, payload: bytes, retido: bool) -> None:
        try:
            msg = contrato.interpretar(topico, payload)
        except contrato.ErroContrato:
            return  # a persistência registra as inválidas
        if msg.tipo == "cmd":
            return
        app.state.difusor.enviar(
            {
                "tipo": msg.tipo,
                "deviceId": msg.device_id,
                "retido": retido,
                "dados": msg.dados.model_dump(mode="json", exclude_none=True),
            }
        )

    app.state.ao_receber = ao_receber  # usado nos testes
    app.include_router(rotas)
    return app


app = criar_app()
