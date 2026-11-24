"""Estado da garagem em memória, montado só a partir do MQTT.

O motorista não usa o banco: as mensagens retidas (estado das vagas e
status) reconstroem tudo assim que o serviço assina, e o app do celular
só precisa do agora.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from comum import contrato
from comum.horario import agora_utc

Chave = tuple[str, int]  # (deviceId, vaga)

# A tag costuma ser lida um pouco antes de o ultrassônico confirmar a
# ocupação (3 leituras seguidas); leitura dentro desta margem antes do
# início da ocupação ainda conta como sendo do carro que está na vaga.
MARGEM_TAG = timedelta(seconds=60)


@dataclass
class _Tag:
    uid: str
    ts: Optional[datetime]
    momento: datetime  # ts do dispositivo ou, sem ele, a hora do recebimento


class Garagem:
    def __init__(self) -> None:
        self._estados: dict[Chave, contrato.EstadoVaga] = {}
        self._online: dict[str, bool] = {}
        self._tags: dict[Chave, _Tag] = {}
        # Início da ocupação atual de cada vaga: separa a tag do carro que
        # está lá agora da tag de um carro que já saiu
        self._ocupada_desde: dict[Chave, datetime] = {}
        # Fim da última ocupação: tag lida antes disso é do carro que saiu,
        # mesmo dentro da MARGEM_TAG (vaga liberada e ocupada logo depois)
        self._liberada_em: dict[Chave, datetime] = {}

    def aplicar(self, msg: contrato.Mensagem) -> bool:
        """Atualiza com uma mensagem; True se o que o motorista vê mudou."""
        d = msg.dados
        if msg.tipo == "estado":
            chave = (msg.device_id, d.vaga)
            anterior = self._estados.get(chave)
            self._estados[chave] = d
            if d.estado == "ocupada":
                # Republicação de "ocupada" não reinicia a ocupação
                self._ocupada_desde.setdefault(chave, d.ts or agora_utc())
            elif d.estado in ("livre", "reservada"):
                # Vaga vazia: a ocupação acabou. "erro" não encerra: a falha
                # do sensor não quer dizer que o carro saiu
                if self._ocupada_desde.pop(chave, None) is not None:
                    self._liberada_em[chave] = d.ts or agora_utc()
            return anterior is None or anterior.estado != d.estado
        if msg.tipo == "status":
            online = d.status == "online"
            mudou = self._online.get(msg.device_id) != online
            self._online[msg.device_id] = online
            return mudou
        if msg.tipo == "rfid":
            self._tags[(msg.device_id, d.vaga)] = _Tag(d.uid, d.ts, d.ts or agora_utc())
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

    def _tag_da_ocupacao_atual(self, chave: Chave, tag: _Tag) -> bool:
        inicio = self._ocupada_desde.get(chave)
        if inicio is None or tag.momento < inicio - MARGEM_TAG:
            return False
        liberada = self._liberada_em.get(chave)
        return liberada is None or tag.momento > liberada

    def vaga_da_tag(self, uid: str) -> Optional[dict[str, Any]]:
        """Vaga ocupada onde esta tag foi lida por último, durante a ocupação atual.

        Tag lida numa ocupação anterior não conta: o carro dela já saiu, e quem
        está na vaga agora é outro carro (com outra tag ou sem tag).
        """
        uid = uid.upper()
        for chave, tag in self._tags.items():
            if (
                tag.uid == uid
                and chave in self._estados
                and self._estado_visivel(chave) == "ocupada"
                and self._tag_da_ocupacao_atual(chave, tag)
            ):
                return {
                    "deviceId": chave[0],
                    "vaga": chave[1],
                    "lida_em": tag.ts.isoformat() if tag.ts else None,
                }
        return None
