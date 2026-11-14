"""Senhas (bcrypt) e token JWT (CP12, R9)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

ALGORITMO = "HS256"
VALIDADE = timedelta(hours=8)
PERFIS = ("operador", "administrador")


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def senha_confere(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode(), senha_hash.encode())
    except ValueError:
        return False


def criar_token(segredo: str, nome: str, perfil: str) -> str:
    expira = datetime.now(timezone.utc) + VALIDADE
    return jwt.encode({"sub": nome, "perfil": perfil, "exp": expira}, segredo, algorithm=ALGORITMO)


def ler_token(segredo: str, token: str) -> dict:
    """Levanta jwt.PyJWTError se inválido ou vencido."""
    return jwt.decode(token, segredo, algorithms=[ALGORITMO], options={"require": ["sub", "exp"]})
