"""API do app do motorista (celular). Pública, sem login.

uvicorn motorista.app:app — documentação OpenAPI em /docs.

Assina só estado das vagas, status e RFID (usuário MQTT "motorista",
somente leitura). Não expõe distâncias nem UIDs de outros carros.

Reserva: repassa o pedido ao operador (OPERADOR_URL), que é o único que
publica comandos e os registra no banco; a tag cadastrada identifica o
motorista.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path as Caminho

from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Path, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from comum import contrato
from comum.config import configurar_log, opcional
from comum.mqtt import assinar_para_sempre
from comum.tempo_real import Difusor
from comum.web import configurar_web
from motorista.garagem import Garagem

PASTA_PAGINAS = Caminho(__file__).parent / "paginas"
TOPICOS = ["garagem/v1/+/vaga/+/estado", "garagem/v1/+/status", "garagem/v1/+/rfid"]
UID = r"^(?:[0-9A-Fa-f]{2}){4,10}$"


class PedidoReserva(BaseModel):
    uid: str = Field(pattern=UID)
    vaga: int = Field(ge=1)
    deviceId: Optional[str] = None


def _reserva(dados: dict) -> dict:
    """Resposta do operador no formato da API do motorista (camelCase)."""
    return {
        "commandId": dados["command_id"],
        "deviceId": dados["device_id"],
        "vaga": dados["vaga"],
        "status": dados["status"],
        "erro": dados["erro"],
    }


def criar_app(iniciar_mqtt: bool = True, operador: Optional[httpx.AsyncBaseTransport] = None) -> FastAPI:
    """`operador`: transporte HTTP falso nos testes; em produção, OPERADOR_URL."""
    url_operador = opcional("OPERADOR_URL", "http://operador:8000")
    garagem = Garagem()
    difusor = Difusor()
    conexao = {"broker": False}

    async def ao_conectar(cliente) -> None:
        conexao["broker"] = cliente is not None

    async def ao_receber(topico: str, payload: bytes, retido: bool) -> None:
        try:
            msg = contrato.interpretar(topico, payload)
        except contrato.ErroContrato:
            return  # a persistência registra as inválidas
        if garagem.aplicar(msg):
            difusor.enviar({"tipo": "vagas", **garagem.resumo()})

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        configurar_log()
        tarefa = None
        if iniciar_mqtt:
            tarefa = asyncio.create_task(assinar_para_sempre("motorista", TOPICOS, ao_receber, ao_conectar))
        yield
        if tarefa:
            tarefa.cancel()
            with suppress(asyncio.CancelledError):
                await tarefa

    app = FastAPI(title="HorangoTango Park — motorista", version="1.0.0", lifespan=ciclo)
    app.state.garagem = garagem
    app.state.difusor = difusor
    app.state.ao_receber = ao_receber  # usado nos testes

    @app.get("/api/vagas", tags=["vagas"])
    async def vagas():
        """Vagas e quantas estão livres. estado: livre, ocupada, reservada, erro ou sem_dados."""
        return garagem.resumo()

    @app.get("/api/minha-vaga/{uid}", tags=["vagas"])
    async def minha_vaga(uid: str = Path(pattern=r"^(?:[0-9A-Fa-f]{2}){4,10}$")):
        """Onde está o carro com esta tag (vaga ocupada onde ela foi lida por último)."""
        vaga = garagem.vaga_da_tag(uid)
        if vaga is None:
            raise HTTPException(404, "carro com esta tag não está em nenhuma vaga")
        return vaga

    async def chamar_operador(metodo: str, caminho: str, corpo: Optional[dict] = None):
        try:
            async with httpx.AsyncClient(base_url=url_operador, transport=operador, timeout=10) as c:
                r = await c.request(metodo, caminho, json=corpo)
        except httpx.HTTPError:
            raise HTTPException(503, "servidor de reservas indisponível") from None
        if r.status_code >= 400:
            detalhe = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
            return JSONResponse({"detail": detalhe if isinstance(detalhe, str) else "pedido inválido"}, r.status_code)
        return JSONResponse(_reserva(r.json()), r.status_code)

    @app.post("/api/reservas", status_code=202, tags=["reserva"])
    async def reservar(pedido: PedidoReserva):
        """Reserva uma vaga livre para a sua tag (cadastrada pelo administrador).

        Uma reserva ativa por tag; a duração é a configurada na garagem.
        Acompanhe em GET /api/reservas/{commandId}: enviado -> aceito
        (ou rejeitado) -> executado -> confirmado."""
        corpo = {"uid": pedido.uid.upper(), "vaga": pedido.vaga}
        if pedido.deviceId:
            corpo["device_id"] = pedido.deviceId
        return await chamar_operador("POST", "/api/motorista/reservas", corpo)

    @app.get("/api/reservas/{command_id}", tags=["reserva"])
    async def ver_reserva(command_id: str = Path(pattern=r"^c-[0-9a-f]{12}$")):
        """Andamento da reserva."""
        return await chamar_operador("GET", f"/api/motorista/reservas/{command_id}")

    @app.websocket("/ws")
    async def tempo_real(websocket: WebSocket):
        """Envia o resumo das vagas ao conectar e a cada mudança."""
        await websocket.accept()
        await difusor.atender(websocket, iniciais=[{"tipo": "vagas", **garagem.resumo()}])

    @app.get("/saude", tags=["saúde"])
    async def saude():
        return {"servico": "motorista", "broker": conexao["broker"]}

    configurar_web(app, PASTA_PAGINAS)
    return app


app = criar_app()
