"""Payloads do CONTRATOS.md: os exemplos passam, os desvios são recusados com motivo."""
import pytest

from comum import contrato
from tests.conftest import BASE, DEVICE, carga

EXEMPLOS = [
    (
        f"{BASE}/telemetria",
        b'{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:30:00Z",'
        b' "vagas": [ { "vaga": 1, "distancia": { "valor": 12.4, "unidade": "cm" } },'
        b' { "vaga": 2, "distancia": null } ],'
        b' "rssi": { "valor": -61, "unidade": "dBm" }, "uptime": { "valor": 384210, "unidade": "ms" } }',
    ),
    (
        # Campo opcional leitor_rfid (ADR 0007)
        f"{BASE}/telemetria",
        b'{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:30:00Z",'
        b' "vagas": [ { "vaga": 1, "distancia": { "valor": 12.4, "unidade": "cm" }, "leitor_rfid": "ok" },'
        b' { "vaga": 2, "distancia": { "valor": 182.0, "unidade": "cm" }, "leitor_rfid": "falha" } ],'
        b' "rssi": { "valor": -61, "unidade": "dBm" }, "uptime": { "valor": 384210, "unidade": "ms" } }',
    ),
    (
        f"{BASE}/vaga/1/estado",
        b'{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:30:05Z",'
        b' "vaga": 1, "estado": "ocupada", "distancia": { "valor": 12.4, "unidade": "cm" } }',
    ),
    (
        f"{BASE}/rfid",
        b'{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:30:07Z",'
        b' "vaga": 1, "uid": "04A1B2C3" }',
    ),
    (
        f"{BASE}/ack",
        b'{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:31:01Z",'
        b' "commandId": "c-0042", "resultado": "rejeitado", "erro": "VAGA_OCUPADA" }',
    ),
    (
        f"{BASE}/status",
        b'{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "ts": "2026-10-27T21:29:50Z",'
        b' "status": "online", "firmware": "1.0.0", "vagas": [1, 2] }',
    ),
    # Last Will: sem ts
    (f"{BASE}/status", b'{ "v": 1, "deviceId": "esp32-garagem-240ac4000110", "status": "offline" }'),
    (
        f"{BASE}/cmd",
        b'{ "v": 1, "commandId": "c-0042", "ts": "2026-10-27T21:31:00Z",'
        b' "acao": "reservar", "vaga": 1, "duracao_min": 15 }',
    ),
    # Relógio nunca sincronizado
    (
        f"{BASE}/vaga/2/estado",
        carga(v=1, deviceId=DEVICE, ts=None, ts_sincronizado=False, uptime_ms=5000, vaga=2, estado="erro", distancia=None),
    ),
    # PAYLOAD_INVALIDO sem commandId legível
    (f"{BASE}/ack", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:31:01Z", commandId=None, resultado="rejeitado", erro="PAYLOAD_INVALIDO")),
]


@pytest.mark.parametrize("topico,payload", EXEMPLOS)
def test_exemplos_do_contrato_sao_aceitos(topico, payload):
    msg = contrato.interpretar(topico, payload)
    assert msg.device_id == DEVICE


INVALIDOS = [
    (
        f"{BASE}/telemetria",
        carga(
            v=1, deviceId=DEVICE, ts="2026-10-27T21:30:00Z",
            vagas=[{"vaga": 1, "distancia": None, "leitor_rfid": "desligado"}],
            rssi={"valor": -61, "unidade": "dBm"}, uptime={"valor": 1, "unidade": "ms"},
        ),
        "leitor_rfid",
    ),
    ("garagem/vaga/1", b"livre", "tópico fora"),  # tópico da N1
    (f"{BASE}/vaga/1/estado", b"ocupada", "json"),
    (f"{BASE}/vaga/1/estado", carga(v=2, deviceId=DEVICE, ts="2026-10-27T21:30:05Z", vaga=1, estado="livre", distancia=None), "v"),
    (f"{BASE}/vaga/1/estado", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:30:05Z", vaga=1, estado="talvez", distancia=None), "estado"),
    (f"{BASE}/vaga/1/estado", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:30:05Z", vaga=2, estado="livre", distancia=None), "difere do tópico"),
    (f"{BASE}/vaga/1/estado", carga(v=1, deviceId="outro", ts="2026-10-27T21:30:05Z", vaga=1, estado="livre", distancia=None), "deviceId"),
    (f"{BASE}/vaga/1/estado", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:30:05", vaga=1, estado="livre", distancia=None), "fuso"),
    (f"{BASE}/vaga/1/estado", carga(v=1, deviceId=DEVICE, ts=None, vaga=1, estado="livre", distancia=None), "ts"),
    (f"{BASE}/vaga/1/estado", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:30:05Z", vaga=1, estado="erro", distancia={"valor": 3, "unidade": "cm"}), "erro"),
    (f"{BASE}/vaga/1/estado", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:30:05Z", vaga=1, estado="livre", distancia={"valor": "12", "unidade": "cm"}), "valor"),
    (f"{BASE}/vaga/1/estado", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:30:05Z", vaga=1, estado="livre", distancia=None, extra=1), "extra"),
    (f"{BASE}/rfid", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:30:07Z", vaga=1, uid="04:A1:B2:C3"), "uid"),
    (f"{BASE}/ack", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:31:01Z", commandId="c-1", resultado="rejeitado"), "erro"),
    (f"{BASE}/ack", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:31:01Z", commandId="c-1", resultado="rejeitado", erro="OUTRO"), "erro"),
    (f"{BASE}/status", carga(v=1, deviceId=DEVICE, ts="2026-10-27T21:29:50Z", status="online"), "firmware"),
]


@pytest.mark.parametrize("topico,payload,trecho", INVALIDOS)
def test_desvios_sao_recusados_com_motivo(topico, payload, trecho):
    with pytest.raises(contrato.ErroContrato) as erro:
        contrato.interpretar(topico, payload)
    assert str(erro.value)  # motivo não vazio, vai para mensagens_invalidas
