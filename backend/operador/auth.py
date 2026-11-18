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


TAMANHO_MINIMO_SEGREDO = 32


def validar_segredo(segredo: str) -> str:
    """Recusa o segredo do .env.example e segredos curtos: com eles, qualquer
    um forjaria um token de administrador."""
    if len(segredo) < TAMANHO_MINIMO_SEGREDO or "troque" in segredo:
        raise RuntimeError(
            "JWT_SEGREDO fraco: use um texto aleatório de 32+ caracteres "
            '(python -c "import secrets; print(secrets.token_urlsafe(48))")'
        )
    return segredo


class LimiteTentativas:
    """Bloqueia o login após `maximo` erros seguidos do mesmo IP e usuário
    dentro de `janela` (força bruta). Fica na memória do processo."""

    def __init__(self, maximo: int = 5, janela: timedelta = timedelta(minutes=5)) -> None:
        self.maximo = maximo
        self.janela = janela
        self._erros: dict[tuple[str, str], list[datetime]] = {}

    def _recentes(self, chave: tuple[str, str], agora: datetime) -> list[datetime]:
        erros = [t for t in self._erros.get(chave, []) if agora - t < self.janela]
        if erros:
            self._erros[chave] = erros
        else:
            self._erros.pop(chave, None)
        return erros

    def bloqueado(self, ip: str, usuario: str, agora: datetime) -> bool:
        return len(self._recentes((ip, usuario), agora)) >= self.maximo

    def registrar_erro(self, ip: str, usuario: str, agora: datetime) -> None:
        self._erros.setdefault((ip, usuario), []).append(agora)

    def limpar(self, ip: str, usuario: str) -> None:
        self._erros.pop((ip, usuario), None)
