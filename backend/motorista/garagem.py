"""Estado da garagem em memória, montado só a partir do MQTT.

O motorista não usa o banco: as mensagens retidas (estado das vagas e
status) reconstroem tudo assim que o serviço assina, e o app do celular
só precisa do agora.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from comum import contrato

Chave = tuple[str, int]  # (deviceId, vaga)


@dataclass
class _Tag:
    uid: str
    ts: Optional[datetime]


class Garagem:
    def __init__(self) -> None:
        self._estados: dict[Chave, contrato.EstadoVaga] = {}
        self._online: dict[str, bool] = {}
        self._tags: dict[Chave, _Tag] = {}

    def aplicar(self, msg: contrato.Mensagem) -> bool:
        """Atualiza com uma mensagem; True se o que o motorista vê mudou."""
        d = msg.dados
        if msg.tipo == "estado":
            chave = (msg.device_id, d.vaga)
            anterior = self._estados.get(chave)
            self._estados[chave] = d
            return anterior is None or anterior.estado != d.estado
        if msg.tipo == "status":
            online = d.status == "online"
            mudou = self._online.get(msg.device_id) != online
            self._online[msg.device_id] = online
            return mudou
        if msg.tipo == "rfid":
            self._tags[(msg.device_id, d.vaga)] = _Tag(d.uid, d.ts)
        return False

    def _estado_visivel(self, chave: Chave) -> str:
        # Dispositivo offline: o último estado pode estar velho
        if self._online.get(chave[0]) is False:
            return "sem_dados"
        return self._estados[chave].estado

    def resumo(self) -> dict[str, Any]:
        vagas = [
            {
                "deviceId": chave[0],
                "vaga": chave[1],
                "estado": self._estado_visivel(chave),
                "desde": self._estados[chave].ts.isoformat() if self._estados[chave].ts else None,
            }
            for chave in sorted(self._estados)
        ]
        return {"livres": sum(v["estado"] == "livre" for v in vagas), "total": len(vagas), "vagas": vagas}

    def vaga_da_tag(self, uid: str) -> Optional[dict[str, Any]]:
        """Vaga ocupada onde esta tag foi lida por último (o carro ainda está lá)."""
        uid = uid.upper()
        for chave, tag in self._tags.items():
            if tag.uid == uid and chave in self._estados and self._estado_visivel(chave) == "ocupada":
                return {
                    "deviceId": chave[0],
                    "vaga": chave[1],
                    "lida_em": tag.ts.isoformat() if tag.ts else None,
                }
        return None
