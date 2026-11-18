"""Indicadores dos gráficos (R14): cálculo puro e a rota /api/estatisticas."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from operador import estatisticas as est
from persistencia.gravador import Gravador
from tests.conftest import BASE, DEVICE, carga, estado, status_online
from tests.test_operador import entrar

# Fixtures cliente/publicador vêm de test_operador
from tests.test_operador import cliente, publicador  # noqa: F401

UTC = timezone.utc


def h(hora, minuto=0, dia=14):
    return datetime(2026, 11, dia, hora, minuto, tzinfo=UTC)


def test_intervalos_recortados_no_periodo():
    ivs = est.intervalos([(h(10), "ocupada"), (h(11, 30), "livre")], "livre", h(9), h(12))
    assert [(i.inicio, i.fim, i.estado, i.completo) for i in ivs] == [
        (h(9), h(10), "livre", False),          # começou antes do período
        (h(10), h(11, 30), "ocupada", True),
        (h(11, 30), h(12), "livre", False),     # ainda não terminou
    ]


def test_intervalos_sem_estado_inicial_comecam_no_primeiro_evento():
    ivs = est.intervalos([(h(10), "livre")], None, h(9), h(11))
    assert [(i.inicio, i.estado) for i in ivs] == [(h(10), "livre")]


def test_intervalos_ignoram_eventos_fora_do_periodo():
    ivs = est.intervalos([(h(8), "ocupada"), (h(13), "livre")], None, h(9), h(12))
    assert [(i.inicio, i.fim, i.estado) for i in ivs] == [(h(9), h(12), "ocupada")]


def test_ocupacao_por_hora_no_fuso_local():
    ivs = est.intervalos([(h(10), "ocupada"), (h(11, 30), "livre")], "livre", h(9), h(12))
    utc = est.ocupacao_por_hora(ivs, UTC)
    assert (utc[9], utc[10], utc[11]) == (0.0, 1.0, 0.5)
    assert utc[12] is None  # hora não observada
    sp = est.ocupacao_por_hora(ivs, ZoneInfo("America/Sao_Paulo"))  # UTC-3
    assert (sp[6], sp[7], sp[8]) == (0.0, 1.0, 0.5)


def test_tempo_em_erro_nao_conta_como_observado():
    ivs = est.intervalos([(h(10, 30), "erro")], "ocupada", h(10), h(11))
    assert est.ocupacao_por_hora(ivs, UTC)[10] == 1.0  # só os 30 min com leitura
    assert est.minutos_em(ivs, "erro") == 30.0


def test_permanencia_so_conta_estadias_completas():
    eventos = [(h(9), "ocupada"), (h(9, 30), "livre"), (h(10), "ocupada"), (h(11, 30), "livre"), (h(11, 50), "ocupada")]
    ivs = est.intervalos(eventos, "ocupada", h(8), h(12))
    assert est.permanencia_media_min(ivs) == (60.0, 2)  # (30 + 90) / 2; a primeira e a última ficam de fora
    assert est.permanencia_media_min([]) == (None, 0)


def test_fracao_online():
    ivs = est.intervalos([(h(10), "offline"), (h(10, 15), "online")], "online", h(9), h(11))
    assert est.fracao_em(ivs, "online") == 0.875
    assert est.fracao_em([], "online") is None


async def test_rota_estatisticas(cliente, sessoes):
    g = Gravador(sessoes)
    await g.tratar(*status_online(ts="2026-11-14T09:00:00Z"))
    for ts, valor in [("2026-11-14T09:00:00Z", "livre"), ("2026-11-14T10:00:00Z", "ocupada"),
                      ("2026-11-14T10:30:00Z", "livre"), ("2026-11-14T11:00:00Z", "erro")]:
        await g.tratar(*estado(1, valor, ts, None if valor == "erro" else 50.0))
    await g.tratar(f"{BASE}/status", carga(v=1, deviceId=DEVICE, ts="2026-11-14T11:30:00Z", status="online",
                                           firmware="0.10.0", vagas=[1, 2]))
    op = await entrar(cliente, "op", "senha-operador")
    r = await cliente.get("/api/estatisticas", headers=op, params={
        "desde": "2026-11-14T09:00:00Z", "ate": "2026-11-14T12:00:00Z", "fuso": "UTC",
    })
    assert r.status_code == 200, r.text
    corpo = r.json()
    vaga1 = next(v for v in corpo["vagas"] if v["vaga"] == 1)
    assert vaga1["ocupacao_por_hora"][9:11] == [0.0, 0.5]
    assert (vaga1["permanencia_media_min"], vaga1["permanencias"], vaga1["tempo_em_erro_min"]) == (30.0, 1, 60.0)
    vaga2 = next(v for v in corpo["vagas"] if v["vaga"] == 2)
    assert vaga2["ocupacao_por_hora"] == [None] * 24  # sem leituras
    assert corpo["dispositivos"][0]["uptime"] == 1.0


@pytest.mark.parametrize("params", [
    {"fuso": "Marte/Olympus"},
    {"desde": "2026-11-14T12:00:00Z", "ate": "2026-11-14T09:00:00Z"},
])
async def test_rota_estatisticas_recusa_parametros_ruins(cliente, params):
    op = await entrar(cliente, "op", "senha-operador")
    assert (await cliente.get("/api/estatisticas", headers=op, params=params)).status_code == 422


async def test_rota_estatisticas_padrao_ultimos_7_dias(cliente):
    op = await entrar(cliente, "op", "senha-operador")
    corpo = (await cliente.get("/api/estatisticas", headers=op)).json()
    desde, ate = (datetime.fromisoformat(corpo[k]) for k in ("desde", "ate"))
    assert ate - desde == timedelta(days=7) and corpo["fuso"] == "America/Sao_Paulo"
