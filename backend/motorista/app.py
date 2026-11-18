"""API do app do motorista (celular). Pública, sem login.

uvicorn motorista.app:app — documentação OpenAPI em /docs.

Assina só estado das vagas, status e RFID (usuário MQTT "motorista",
somente leitura). Não expõe distâncias nem UIDs de outros carros.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path as Caminho

from fastapi import FastAPI, HTTPException, Path, WebSocket

from comum import contrato
from comum.config import configurar_log
from comum.mqtt import assinar_para_sempre
from comum.tempo_real import Difusor
from comum.web import configurar_web
from motorista.garagem import Garagem

PASTA_PAGINAS = Caminho(__file__).parent / "paginas"
TOPICOS = ["garagem/v1/+/vaga/+/estado", "garagem/v1/+/status", "garagem/v1/+/rfid"]


def criar_app(iniciar_mqtt: bool = True) -> FastAPI:
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
