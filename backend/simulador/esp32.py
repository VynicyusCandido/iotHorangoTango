"""Comportamento do firmware (src/garagem/garagem.ino), sem rede.

Cada método devolve as publicações que o ESP32 faria, na ordem dele. A
conexão com o broker fica em __main__.py; os testes usam esta classe
direto. Regras copiadas do firmware e do ADR 0004:

- estado publicado = erro > reservada > sensor; LED não é simulado;
- comandos validados na mesma ordem do firmware, com os mesmos códigos;
- respostas na ordem aceito -> estado -> executado;
- reserva expira pelo tempo contado no dispositivo;
- carro chegando: a tag é lida antes de o ultrassônico confirmar (3 s).
"""
from __future__ import annotations

import json
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, NamedTuple, Optional

from comum.horario import agora_utc

FIRMWARE = "sim-1.0.0"
HISTORICO_COMANDOS = 16
TAM_COMMAND_ID = 40
DIST_LIVRE_CM = 80.0
DIST_OCUPADA_CM = 10.0


class Publicacao(NamedTuple):
    topico: str
    payload: str
    retain: bool
    qos: int


@dataclass
class VagaSim:
    numero: int
    sensor: str = "livre"            # livre, ocupada ou erro
    distancia: Optional[float] = DIST_LIVRE_CM
    fim_reserva: Optional[datetime] = None
    carro_uid: Optional[str] = None  # tag do carro estacionado (se tiver)


@dataclass
class Configuracao:
    limiar_ocupa_cm: float = 25.0
    limiar_libera_cm: float = 35.0
    duracao_reserva_min: int = 15


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _inteiro(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _numero(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


@dataclass
class SimuladorESP32:
    device_id: str
    numeros: tuple[int, ...] = (1, 2)
    tags: tuple[str, ...] = ("04A1B2C3", "0A0B0C0D", "DEADBEEF")
    agora: Callable[[], datetime] = agora_utc
    aleatorio: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        self.vagas = {n: VagaSim(n) for n in self.numeros}
        self.config = Configuracao()
        self._processados: deque[str] = deque(maxlen=HISTORICO_COMANDOS)
        self._boot = self.agora()

    # ------------------------------------------------------------------
    # Tópicos e cabeçalho
    # ------------------------------------------------------------------

    @property
    def base(self) -> str:
        return f"garagem/v1/{self.device_id}"

    @property
    def topico_cmd(self) -> str:
        return f"{self.base}/cmd"

    def _cabecalho(self) -> dict[str, Any]:
        return {"v": 1, "deviceId": self.device_id, "ts": _iso(self.agora())}

    def _pub(self, sufixo: str, corpo: dict[str, Any], retain: bool = False, qos: int = 1) -> Publicacao:
        return Publicacao(f"{self.base}/{sufixo}", json.dumps(corpo, separators=(",", ":")), retain, qos)

    # ------------------------------------------------------------------
    # Publicações periódicas e de conexão
    # ------------------------------------------------------------------

    def lwt(self) -> tuple[str, str]:
        """Last Will: registrado na conexão, sem ts (como no firmware)."""
        return f"{self.base}/status", json.dumps({"v": 1, "deviceId": self.device_id, "status": "offline"})

    def status_online(self) -> Publicacao:
        corpo = {**self._cabecalho(), "status": "online", "firmware": FIRMWARE, "vagas": list(self.numeros)}
        return self._pub("status", corpo, retain=True)

    def estado_efetivo(self, v: VagaSim) -> str:
        if v.sensor == "erro":
            return "erro"
        if v.fim_reserva is not None:
            return "reservada"
        return v.sensor

    def _estado(self, v: VagaSim) -> Publicacao:
        estado = self.estado_efetivo(v)
        distancia = None if estado == "erro" or v.distancia is None else {"valor": round(v.distancia, 1), "unidade": "cm"}
        corpo = {**self._cabecalho(), "vaga": v.numero, "estado": estado, "distancia": distancia}
        return self._pub(f"vaga/{v.numero}/estado", corpo, retain=True)

    def estados_atuais(self) -> list[Publicacao]:
        """Republicados ao (re)conectar."""
        return [self._estado(v) for v in self.vagas.values()]

    def telemetria(self) -> Publicacao:
        uptime = int((self.agora() - self._boot).total_seconds() * 1000)
        vagas = [
            {"vaga": v.numero,
             "distancia": None if v.distancia is None else {"valor": round(v.distancia, 1), "unidade": "cm"}}
            for v in self.vagas.values()
        ]
        corpo = {
            **self._cabecalho(),
            "vagas": vagas,
            "rssi": {"valor": self.aleatorio.randint(-75, -50), "unidade": "dBm"},
            "uptime": {"valor": uptime, "unidade": "ms"},
        }
        return self._pub("telemetria", corpo, qos=0)

    # ------------------------------------------------------------------
    # Acontecimentos na maquete
    # ------------------------------------------------------------------

    def carro_chega(self, vaga: int, uid: Optional[str] = None) -> list[Publicacao]:
        v = self.vagas[vaga]
        if v.sensor != "livre":
            return []
        saida = []
        if uid:  # o leitor sob o piso lê a tag antes de o ultrassônico confirmar
            saida.append(self._pub("rfid", {**self._cabecalho(), "vaga": vaga, "uid": uid}))
        v.sensor, v.distancia, v.carro_uid = "ocupada", DIST_OCUPADA_CM, uid
        v.fim_reserva = None  # carro chegou: a reserva termina
        saida.append(self._estado(v))
        return saida

    def carro_sai(self, vaga: int) -> list[Publicacao]:
        v = self.vagas[vaga]
        if v.sensor != "ocupada":
            return []
        v.sensor, v.distancia, v.carro_uid = "livre", DIST_LIVRE_CM, None
        return [self._estado(v)]

    def falha_sensor(self, vaga: int) -> list[Publicacao]:
        v = self.vagas[vaga]
        if v.sensor == "erro":
            return []
        v.sensor, v.distancia, v.fim_reserva = "erro", None, None  # falha encerra a reserva
        return [self._estado(v)]

    def sensor_volta(self, vaga: int) -> list[Publicacao]:
        v = self.vagas[vaga]
        if v.sensor != "erro":
            return []
        v.sensor = "ocupada" if v.carro_uid else "livre"
        v.distancia = DIST_OCUPADA_CM if v.carro_uid else DIST_LIVRE_CM
        return [self._estado(v)]

    def verificar_reservas(self) -> list[Publicacao]:
        """Reserva expira pelo tempo contado no dispositivo, mesmo sem rede."""
        saida = []
        for v in self.vagas.values():
            if v.fim_reserva is not None and self.agora() >= v.fim_reserva:
                v.fim_reserva = None
                saida.append(self._estado(v))
        return saida

    def passo_aleatorio(self, chance: float = 0.3) -> list[Publicacao]:
        """Movimento da garagem: carros chegam (com ou sem tag) e saem."""
        if self.aleatorio.random() >= chance:
            return []
        v = self.aleatorio.choice(list(self.vagas.values()))
        if v.sensor == "ocupada":
            return self.carro_sai(v.numero)
        if v.sensor == "livre":
            uid = self.aleatorio.choice(self.tags) if self.tags and self.aleatorio.random() < 0.7 else None
            return self.carro_chega(v.numero, uid)
        return []

    # ------------------------------------------------------------------
    # Comandos (mesma ordem de validação do firmware, ADR 0004)
    # ------------------------------------------------------------------

    def _ack(self, command_id: Optional[str], resultado: str, erro: Optional[str] = None) -> Publicacao:
        corpo = {**self._cabecalho(), "commandId": command_id, "resultado": resultado}
        if erro:
            corpo["erro"] = erro
        return self._pub("ack", corpo)

    def processar_comando(self, payload: bytes) -> list[Publicacao]:
        try:
            doc = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return [self._ack(None, "rejeitado", "PAYLOAD_INVALIDO")]
        if not isinstance(doc, dict):
            return [self._ack(None, "rejeitado", "PAYLOAD_INVALIDO")]

        cid = doc.get("commandId")
        if not isinstance(cid, str) or not cid or len(cid) > TAM_COMMAND_ID:
            return [self._ack(None, "rejeitado", "PAYLOAD_INVALIDO")]
        if cid in self._processados:
            return [self._ack(cid, "rejeitado", "COMANDO_DUPLICADO")]
        self._processados.append(cid)

        if doc.get("v") != 1 or not _inteiro(doc.get("v")):
            return [self._ack(cid, "rejeitado", "PAYLOAD_INVALIDO")]

        acao = doc.get("acao")
        if acao == "reservar":
            return self._reservar(cid, doc)
        if acao == "cancelar_reserva":
            return self._cancelar(cid, doc)
        if acao == "configurar":
            return self._configurar(cid, doc)
        return [self._ack(cid, "rejeitado", "ACAO_DESCONHECIDA")]

    def _ler_vaga(self, cid: str, doc: dict) -> tuple[Optional[VagaSim], list[Publicacao]]:
        if not _inteiro(doc.get("vaga")):
            return None, [self._ack(cid, "rejeitado", "PARAMETRO_INVALIDO")]
        v = self.vagas.get(doc["vaga"])
        if v is None:
            return None, [self._ack(cid, "rejeitado", "VAGA_INEXISTENTE")]
        return v, []

    def _reservar(self, cid: str, doc: dict) -> list[Publicacao]:
        v, erro = self._ler_vaga(cid, doc)
        if v is None:
            return erro
        duracao = self.config.duracao_reserva_min
        if doc.get("duracao_min") is not None:
            if not _inteiro(doc["duracao_min"]) or not 1 <= doc["duracao_min"] <= 60:
                return [self._ack(cid, "rejeitado", "PARAMETRO_INVALIDO")]
            duracao = doc["duracao_min"]
        estado = self.estado_efetivo(v)
        if estado == "erro":
            return [self._ack(cid, "rejeitado", "VAGA_EM_ERRO")]
        if estado != "livre":
            return [self._ack(cid, "rejeitado", "VAGA_OCUPADA")]
        aceito = self._ack(cid, "aceito")
        v.fim_reserva = self.agora() + timedelta(minutes=duracao)
        return [aceito, self._estado(v), self._ack(cid, "executado")]

    def _cancelar(self, cid: str, doc: dict) -> list[Publicacao]:
        v, erro = self._ler_vaga(cid, doc)
        if v is None:
            return erro
        if v.fim_reserva is None:
            return [self._ack(cid, "rejeitado", "SEM_RESERVA")]
        aceito = self._ack(cid, "aceito")
        v.fim_reserva = None
        return [aceito, self._estado(v), self._ack(cid, "executado")]

    def _configurar(self, cid: str, doc: dict) -> list[Publicacao]:
        ocupa, libera, duracao = doc.get("limiar_ocupa_cm"), doc.get("limiar_libera_cm"), doc.get("duracao_reserva_min")
        if not (_numero(ocupa) and _numero(libera) and _inteiro(duracao)):
            return [self._ack(cid, "rejeitado", "PARAMETRO_INVALIDO")]
        if not (2 <= ocupa <= 200 and ocupa + 2 < libera <= 400 and 1 <= duracao <= 60):
            return [self._ack(cid, "rejeitado", "PARAMETRO_INVALIDO")]
        aceito = self._ack(cid, "aceito")
        self.config = Configuracao(float(ocupa), float(libera), duracao)
        return [aceito, self._ack(cid, "executado")]
