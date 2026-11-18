"""Como as páginas chegam ao navegador: servidas pelo próprio serviço ou por CORS.

- Pasta `paginas/` dentro do serviço (operador/paginas, motorista/paginas):
  se existir, é servida na raiz (`/`), como prevê o CP12 (backend serve
  painel e app). A API continua em /api, /ws e /docs.
- CORS_ORIGENS (ex.: "http://192.168.1.50:8080,http://localhost:5500"):
  libera páginas abertas de outro endereço a chamar a API.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import opcional


def configurar_web(app: FastAPI, pasta_paginas: Path) -> None:
    origens = [o.strip() for o in opcional("CORS_ORIGENS").split(",") if o.strip()]
    if origens:
        app.add_middleware(
            CORSMiddleware, allow_origins=origens, allow_methods=["*"], allow_headers=["*"]
        )
    if pasta_paginas.is_dir():
        # Montado por último: as rotas da API têm prioridade
        app.mount("/", StaticFiles(directory=pasta_paginas, html=True), name="paginas")
