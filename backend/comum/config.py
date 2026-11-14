"""Configuração por variáveis de ambiente (vêm do .env pelo compose)."""
from __future__ import annotations

import logging
import os


def obrigatorio(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        raise RuntimeError(f"variável de ambiente {nome} não definida (ver .env.example)")
    return valor


def opcional(nome: str, padrao: str = "") -> str:
    return os.environ.get(nome) or padrao


def configurar_log() -> None:
    logging.basicConfig(
        level=opcional("LOG_NIVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
