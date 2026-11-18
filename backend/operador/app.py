"""API do operador e do administrador (painel web).

uvicorn operador.app:app — documentação OpenAPI em /docs.

Assina garagem/v1/# para repassar ao painel, em tempo real (WebSocket
/ws), tudo o que o dispositivo publica. É o único serviço que publica
comandos (garagem/v1/{id}/cmd) e roda as regras remotas (alertas,
reserva vencida, Telegram). Estado, histórico e andamento dos comandos
vêm do banco, gravado pelo assinante de persistência.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, AsyncIterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from comum import contrato, modelos as m
from comum.config import configurar_log, obrigatorio, opcional
from comum.db import criar_sessoes
from comum.horario import agora_utc, utc
from comum.mqtt import assinar_para_sempre
from comum.tempo_real import Difusor
from comum.web import configurar_web
from operador import auth, estatisticas as est
from operador.comandos import FalhaEnvio, Publicar, enviar_comando
from operador.esquemas import (
    AlertaOut,
    ComandoOut,
    ComandoReq,
    DispositivoEstatisticas,
    DispositivoOut,
    EstadoOut,
    EstatisticasOut,
    LoginReq,
    MedicaoOut,
    RfidOut,
    TagNova,
    TagOut,
    TokenOut,
    TrocaSenha,
    UsuarioNovo,
    UsuarioOut,
    VagaEstatisticas,
    VagaOut,
)
from operador.regras import Vigia
from operador.telegram import Notificar, telegram_do_ambiente

log = logging.getLogger("operador")

PASTA_PAGINAS = Path(__file__).parent / "paginas"

# Compara a senha mesmo com usuário inexistente: a resposta leva o mesmo
# tempo e não revela quais usuários existem
_HASH_FALSO = auth.hash_senha("usuario-inexistente")


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
    s: Sessao,
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
    # Confere no banco: usuário apagado perde o acesso na hora, e o perfil
    # vale o atual, não o da hora do login
    u = await s.scalar(select(m.Usuario).where(m.Usuario.nome == dados["sub"]))
    if u is None:
        raise nao_autorizado
    return UsuarioOut(nome=u.nome, perfil=u.perfil)


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
    limite: auth.LimiteTentativas = request.app.state.limite_login
    ip = request.client.host if request.client else "?"
    agora = agora_utc()
    if limite.bloqueado(ip, req.usuario, agora):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "muitas tentativas; aguarde alguns minutos")
    u = await s.scalar(select(m.Usuario).where(m.Usuario.nome == req.usuario))
    if not auth.senha_confere(req.senha, u.senha_hash if u else _HASH_FALSO) or u is None:
        limite.registrar_erro(ip, req.usuario, agora)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuário ou senha incorretos")
    limite.limpar(ip, req.usuario)
    token = auth.criar_token(request.app.state.jwt_segredo, u.nome, u.perfil)
    return TokenOut(access_token=token, perfil=u.perfil)


@rotas.get("/api/eu", response_model=UsuarioOut, tags=["login"])
async def eu(usuario: Logado):
    return usuario


@rotas.post("/api/eu/senha", status_code=204, tags=["login"])
async def trocar_senha(troca: TrocaSenha, usuario: Logado, s: Sessao):
    u = await s.scalar(select(m.Usuario).where(m.Usuario.nome == usuario.nome))
    if not auth.senha_confere(troca.senha_atual, u.senha_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "senha atual incorreta")
    u.senha_hash = auth.hash_senha(troca.senha_nova)
    await s.commit()
    return Response(status_code=204)


@rotas.get("/api/usuarios", response_model=list[UsuarioOut], tags=["login"])
async def listar_usuarios(_: Admin, s: Sessao):
    return (await s.scalars(select(m.Usuario).order_by(m.Usuario.nome))).all()


@rotas.post("/api/usuarios", response_model=UsuarioOut, status_code=201, tags=["login"])
async def criar_usuario(novo: UsuarioNovo, _: Admin, s: Sessao):
    if await s.scalar(select(m.Usuario).where(m.Usuario.nome == novo.nome)):
        raise HTTPException(status.HTTP_409_CONFLICT, "usuário já existe")
    u = m.Usuario(nome=novo.nome, senha_hash=auth.hash_senha(novo.senha), perfil=novo.perfil, criado_em=agora_utc())
    s.add(u)
    await s.commit()
    return u


@rotas.delete("/api/usuarios/{nome}", status_code=204, tags=["login"])
async def apagar_usuario(nome: str, admin: Admin, s: Sessao):
    if nome == admin.nome:
        raise HTTPException(status.HTTP_409_CONFLICT, "não é possível apagar o próprio usuário")
    u = await s.scalar(select(m.Usuario).where(m.Usuario.nome == nome))
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
    await s.delete(u)
    await s.commit()
    return Response(status_code=204)


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
    """Tags lidas; `apelido` vem preenchido se a tag estiver cadastrada."""
    q = select(m.LeituraRfid)
    if device_id:
        q = q.where(m.LeituraRfid.device_id == device_id)
    if vaga is not None:
        q = q.where(m.LeituraRfid.vaga == vaga)
    if desde:
        q = q.where(m.LeituraRfid.ts >= desde)
    leituras = (await s.scalars(q.order_by(m.LeituraRfid.ts.desc()).limit(limite))).all()
    uids = {r.uid for r in leituras}
    apelidos = {
        t.uid: t.apelido for t in (await s.scalars(select(m.Tag).where(m.Tag.uid.in_(uids)))).all()
    } if uids else {}
    return [RfidOut.model_validate(r).model_copy(update={"apelido": apelidos.get(r.uid)}) for r in leituras]


@rotas.get("/api/alertas", response_model=list[AlertaOut], tags=["consulta"])
async def alertas(_: Logado, s: Sessao, ativos: bool = True, limite: Limite = 100):
    q = select(m.Alerta)
    if ativos:
        q = q.where(m.Alerta.resolvido_em.is_(None))
    return (await s.scalars(q.order_by(m.Alerta.criado_em.desc()).limit(limite))).all()


@rotas.post("/api/alertas/{alerta_id}/resolver", response_model=AlertaOut, tags=["consulta"])
async def resolver_alerta(alerta_id: int, _: Logado, s: Sessao):
    """Marca como resolvido (ex.: reserva expirada já vista pelo operador)."""
    alerta = await s.get(m.Alerta, alerta_id)
    if alerta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alerta não encontrado")
    if alerta.resolvido_em is None:
        alerta.resolvido_em = agora_utc()
        await s.commit()
    return alerta


@rotas.get("/api/estatisticas", response_model=EstatisticasOut, tags=["consulta"])
async def estatisticas(
    _: Logado,
    s: Sessao,
    desde: Optional[datetime] = Query(default=None, description="padrão: 7 dias antes de `ate`"),
    ate: Optional[datetime] = Query(default=None, description="padrão: agora"),
    fuso: str = Query(default="America/Sao_Paulo", description="fuso das horas do dia"),
):
    """Indicadores para os gráficos (R14): ocupação por hora do dia, tempo
    médio de permanência, tempo em erro por vaga e uptime por dispositivo."""
    try:
        zona = ZoneInfo(fuso)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"fuso desconhecido: {fuso}") from None
    ate = utc(ate) if ate else agora_utc()
    desde = utc(desde) if desde else ate - timedelta(days=7)
    if desde >= ate:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "`desde` precisa ser antes de `ate`")

    saida_vagas, saida_disp = [], []
    for d in (await s.scalars(select(m.Dispositivo).order_by(m.Dispositivo.device_id))).all():
        for numero in d.vagas or []:
            filtro = (m.EstadoVaga.device_id == d.device_id, m.EstadoVaga.vaga == numero)
            anterior = await s.scalar(
                select(m.EstadoVaga.estado).where(*filtro, m.EstadoVaga.ts <= desde)
                .order_by(m.EstadoVaga.ts.desc(), m.EstadoVaga.id.desc()).limit(1)
            )
            linhas = (
                await s.execute(
                    select(m.EstadoVaga.ts, m.EstadoVaga.estado)
                    .where(*filtro, m.EstadoVaga.ts > desde, m.EstadoVaga.ts <= ate)
                    .order_by(m.EstadoVaga.ts, m.EstadoVaga.id)
                )
            ).all()
            ivs = est.intervalos([(utc(ts), e) for ts, e in linhas], anterior, desde, ate)
            media, quantidade = est.permanencia_media_min(ivs)
            saida_vagas.append(
                VagaEstatisticas(
                    device_id=d.device_id,
                    vaga=numero,
                    ocupacao_por_hora=est.ocupacao_por_hora(ivs, zona),
                    permanencia_media_min=media,
                    permanencias=quantidade,
                    tempo_em_erro_min=est.minutos_em(ivs, "erro"),
                )
            )

        filtro = (m.EventoStatus.device_id == d.device_id,)
        anterior = await s.scalar(
            select(m.EventoStatus.status).where(*filtro, m.EventoStatus.ts <= desde)
            .order_by(m.EventoStatus.ts.desc(), m.EventoStatus.id.desc()).limit(1)
        )
        linhas = (
            await s.execute(
                select(m.EventoStatus.ts, m.EventoStatus.status)
                .where(*filtro, m.EventoStatus.ts > desde, m.EventoStatus.ts <= ate)
                .order_by(m.EventoStatus.ts, m.EventoStatus.id)
            )
        ).all()
        ivs = est.intervalos([(utc(ts), st) for ts, st in linhas], anterior, desde, ate)
        saida_disp.append(
            DispositivoEstatisticas(
                device_id=d.device_id, uptime=est.fracao_em(ivs, "online"), tempo_offline_min=est.minutos_em(ivs, "offline")
            )
        )
    return EstatisticasOut(desde=desde, ate=ate, fuso=fuso, vagas=saida_vagas, dispositivos=saida_disp)


# ----------------------------------------------------------------------
# Tags RFID cadastradas (R16)
# ----------------------------------------------------------------------


@rotas.get("/api/tags", response_model=list[TagOut], tags=["tags"])
async def listar_tags(_: Logado, s: Sessao):
    return (await s.scalars(select(m.Tag).order_by(m.Tag.apelido))).all()


@rotas.post("/api/tags", response_model=TagOut, status_code=201, tags=["tags"])
async def cadastrar_tag(nova: TagNova, _: Admin, s: Sessao):
    uid = nova.uid.upper()
    if await s.get(m.Tag, uid):
        raise HTTPException(status.HTTP_409_CONFLICT, "tag já cadastrada")
    tag = m.Tag(uid=uid, apelido=nova.apelido, dono=nova.dono, criada_em=agora_utc())
    s.add(tag)
    await s.commit()
    return tag


@rotas.delete("/api/tags/{uid}", status_code=204, tags=["tags"])
async def remover_tag(uid: str, _: Admin, s: Sessao):
    tag = await s.get(m.Tag, uid.upper())
    if tag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tag não cadastrada")
    await s.delete(tag)
    await s.commit()
    return Response(status_code=204)


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
async def enviar(req: Annotated[ComandoReq, Body()], usuario: Admin, request: Request, s: Sessao):
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
    try:
        return await enviar_comando(
            s,
            request.app.state.publicar,
            device_id=disp.device_id,
            acao=req.acao,
            vaga=vaga,
            parametros=req.model_dump(exclude={"acao", "device_id", "vaga"}, exclude_none=True),
            origem=usuario.nome,
            agora=agora_utc(),
        )
    except FalhaEnvio:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "broker indisponível; comando não enviado") from None


# ----------------------------------------------------------------------
# Tempo real e saúde
# ----------------------------------------------------------------------


@rotas.websocket("/ws")
async def tempo_real(websocket: WebSocket, token: str = ""):
    """Eventos do dispositivo e alertas ao vivo. Conecte em /ws?token=<access_token>."""
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
    if "troque" in senha:
        log.warning("ADMIN_SENHA ainda é a do .env.example; troque pelo painel (POST /api/eu/senha)")
    async with sessoes() as s, s.begin():
        if await s.scalar(select(m.Usuario).where(m.Usuario.nome == nome)) is None:
            s.add(m.Usuario(nome=nome, senha_hash=auth.hash_senha(senha), perfil="administrador", criado_em=agora_utc()))
            log.info("administrador inicial criado: %s", nome)


def criar_app(
    sessoes: Optional[async_sessionmaker[AsyncSession]] = None,
    *,
    jwt_segredo: Optional[str] = None,
    publicar: Optional[Publicar] = None,
    notificar: Optional[Notificar] = None,
    iniciar_mqtt: bool = True,
) -> FastAPI:
    """Sem argumentos, lê tudo do ambiente ao subir (produção). Os testes
    passam sessões, segredo e um `publicar` falso e não sobem o MQTT nem
    as regras."""

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        configurar_log()
        if app.state.sessoes is None:
            app.state.sessoes = criar_sessoes()
        if app.state.jwt_segredo is None:
            app.state.jwt_segredo = auth.validar_segredo(obrigatorio("JWT_SEGREDO"))
        await _criar_admin_inicial(app.state.sessoes)
        tarefas = []
        if iniciar_mqtt:
            vigia = Vigia(
                app.state.sessoes, app.state.publicar, notificar or telegram_do_ambiente(), app.state.difusor
            )
            tarefas = [
                asyncio.create_task(assinar_para_sempre("operador", ["garagem/v1/#"], ao_receber, ao_conectar)),
                asyncio.create_task(vigia.rodar_para_sempre()),
            ]
        yield
        for tarefa in tarefas:
            tarefa.cancel()
            with suppress(asyncio.CancelledError):
                await tarefa

    app = FastAPI(title="HorangoTango Park — operador", version="1.1.0", lifespan=ciclo)
    app.state.sessoes = sessoes
    app.state.jwt_segredo = jwt_segredo
    app.state.cliente_mqtt = None
    app.state.difusor = Difusor()
    app.state.limite_login = auth.LimiteTentativas()

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
    configurar_web(app, PASTA_PAGINAS)
    return app


app = criar_app()
