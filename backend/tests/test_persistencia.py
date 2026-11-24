"""Assinante de persistência: o que vai para o banco e o ciclo do comando (R7)."""
from datetime import datetime, timezone

from sqlalchemy import func, select

from comum import modelos as m
from persistencia.gravador import Gravador
from tests.conftest import BASE, DEVICE, ack, carga, estado, rfid, status_online

RECEBIDO = datetime(2026, 11, 12, 10, 5, tzinfo=timezone.utc)


def gravador(sessoes):
    return Gravador(sessoes, agora=lambda: RECEBIDO)


async def contar(sessoes, modelo):
    async with sessoes() as s:
        return await s.scalar(select(func.count()).select_from(modelo))


async def criar_comando(sessoes, command_id, acao="reservar", vaga=1):
    async with sessoes() as s, s.begin():
        s.add(
            m.Comando(
                command_id=command_id, device_id=DEVICE, acao=acao, vaga=vaga, parametros={},
                origem="admin", status="enviado", enviado_em=datetime(2026, 11, 12, 10, 0, 30, tzinfo=timezone.utc),
            )
        )


async def comando(sessoes, command_id):
    async with sessoes() as s:
        return await s.scalar(select(m.Comando).where(m.Comando.command_id == command_id))


async def test_status_cria_dispositivo_online(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    async with sessoes() as s:
        d = await s.get(m.Dispositivo, DEVICE)
    assert d.online and d.firmware == "0.10.0" and d.vagas == [1, 2]
    assert await contar(sessoes, m.EventoStatus) == 1

    await g.tratar(f"{BASE}/status", carga(v=1, deviceId=DEVICE, status="offline"))  # Last Will
    async with sessoes() as s:
        assert not (await s.get(m.Dispositivo, DEVICE)).online


async def test_invalida_vai_para_mensagens_invalidas(sessoes):
    await gravador(sessoes).tratar(f"{BASE}/vaga/1/estado", b"ocupada")
    async with sessoes() as s:
        inv = await s.scalar(select(m.MensagemInvalida))
    assert inv.payload == "ocupada" and inv.motivo
    assert await contar(sessoes, m.EstadoVaga) == 0


async def test_estado_retido_reentregue_nao_duplica(sessoes):
    g = gravador(sessoes)
    await g.tratar(*estado(1, "livre", "2026-11-12T10:00:00Z", 80.0))
    await g.tratar(*estado(1, "livre", "2026-11-12T10:00:00Z", 80.0), retido=True)
    # Republicação após reconectar: mesmo estado, ts novo
    await g.tratar(*estado(1, "livre", "2026-11-12T10:03:00Z", 80.0))
    await g.tratar(*estado(1, "ocupada", "2026-11-12T10:04:00Z"))
    async with sessoes() as s:
        estados = (await s.scalars(select(m.EstadoVaga.estado).order_by(m.EstadoVaga.ts))).all()
    assert estados == ["livre", "ocupada"]


async def test_rfid_repetido_nao_duplica(sessoes):
    g = gravador(sessoes)
    await g.tratar(*rfid(1, "04A1B2C3", "2026-11-12T10:00:07Z"))
    await g.tratar(*rfid(1, "04A1B2C3", "2026-11-12T10:00:07Z"))  # PUBACK perdido, publicou de novo
    await g.tratar(*rfid(1, "04A1B2C3", "2026-11-12T10:20:00Z"))  # nova visita
    assert await contar(sessoes, m.LeituraRfid) == 2


async def test_telemetria_grava_distancias_e_rssi(sessoes):
    await gravador(sessoes).tratar(
        f"{BASE}/telemetria",
        carga(
            v=1, deviceId=DEVICE, ts="2026-11-12T10:00:00Z",
            vagas=[{"vaga": 1, "distancia": {"valor": 12.4, "unidade": "cm"}}, {"vaga": 2, "distancia": None}],
            rssi={"valor": -61, "unidade": "dBm"}, uptime={"valor": 384210, "unidade": "ms"},
        ),
    )
    async with sessoes() as s:
        linhas = (await s.execute(select(m.Medicao.grandeza, m.Medicao.vaga, m.Medicao.valor))).all()
    assert sorted(linhas, key=str) == sorted([("distancia", 1, 12.4), ("rssi", None, -61.0)], key=str)


def telemetria_leitores(ts, leitor1, leitor2=None):
    vagas = [{"vaga": 1, "distancia": None, "leitor_rfid": leitor1}]
    vagas.append({"vaga": 2, "distancia": None} if leitor2 is None else {"vaga": 2, "distancia": None, "leitor_rfid": leitor2})
    return f"{BASE}/telemetria", carga(
        v=1, deviceId=DEVICE, ts=ts, vagas=vagas,
        rssi={"valor": -61, "unidade": "dBm"}, uptime={"valor": 384210, "unidade": "ms"},
    )


async def test_telemetria_guarda_estado_do_leitor_rfid(sessoes):
    g = gravador(sessoes)
    await g.tratar(*telemetria_leitores("2026-11-23T21:00:00Z", "ok"))
    await g.tratar(*telemetria_leitores("2026-11-23T21:00:10Z", "ok"))
    async with sessoes() as s:
        leitores = (await s.get(m.Dispositivo, DEVICE)).leitores_rfid
    # "desde" fica no início do estado; vaga 2 sem o campo (firmware antigo) não entra
    assert leitores == {"1": {"estado": "ok", "desde": "2026-11-23T21:00:00+00:00",
                              "atualizado_em": "2026-11-23T21:00:10+00:00"}}

    await g.tratar(*telemetria_leitores("2026-11-23T21:00:20Z", "falha", "ok"))
    async with sessoes() as s:
        leitores = (await s.get(m.Dispositivo, DEVICE)).leitores_rfid
    assert leitores["1"]["estado"] == "falha" and leitores["1"]["desde"] == "2026-11-23T21:00:20+00:00"
    assert leitores["2"]["estado"] == "ok"


async def test_telemetria_retida_nao_altera_leitor_rfid(sessoes):
    g = gravador(sessoes)
    await g.tratar(*telemetria_leitores("2026-11-23T21:00:00Z", "falha"), retido=True)
    async with sessoes() as s:
        assert (await s.get(m.Dispositivo, DEVICE)).leitores_rfid is None


async def test_ts_nulo_estimado_pela_telemetria(sessoes):
    g = gravador(sessoes)
    # Telemetria (ao vivo) diz: uptime 600000 ms = 10:04:00
    await g.tratar(
        f"{BASE}/telemetria",
        carga(
            v=1, deviceId=DEVICE, ts="2026-11-12T10:04:00Z", vagas=[],
            rssi={"valor": -61, "unidade": "dBm"}, uptime={"valor": 600000, "unidade": "ms"},
        ),
    )
    # Evento da fila, sem NTP na hora, com uptime 540000 ms -> 1 min antes
    await g.tratar(
        f"{BASE}/vaga/1/estado",
        carga(v=1, deviceId=DEVICE, ts=None, ts_sincronizado=False, uptime_ms=540000, vaga=1, estado="ocupada",
              distancia={"valor": 10.0, "unidade": "cm"}),
    )
    async with sessoes() as s:
        e = await s.scalar(select(m.EstadoVaga))
    assert e.ts_estimado
    assert e.ts.replace(tzinfo=None) == datetime(2026, 11, 12, 10, 3)


async def test_ciclo_reservar_ate_confirmado(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await g.tratar(*estado(1, "livre", "2026-11-12T10:00:00Z", 80.0))
    await criar_comando(sessoes, "c-1")

    # Ordem do firmware: aceito -> estado reservada -> executado
    await g.tratar(*ack("c-1", "aceito"))
    assert (await comando(sessoes, "c-1")).status == "aceito"
    await g.tratar(*estado(1, "reservada", "2026-11-12T10:01:00Z", 80.0))
    cmd = await comando(sessoes, "c-1")
    assert cmd.status == "aceito" and cmd.confirmado_em is not None
    await g.tratar(*ack("c-1", "executado"))
    cmd = await comando(sessoes, "c-1")
    assert cmd.status == "confirmado" and cmd.executado_em is not None and cmd.recebido_em is not None


async def test_rejeitado_guarda_o_erro(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await criar_comando(sessoes, "c-2")
    await g.tratar(*ack("c-2", "rejeitado", erro="VAGA_OCUPADA"))
    cmd = await comando(sessoes, "c-2")
    assert cmd.status == "rejeitado" and cmd.erro == "VAGA_OCUPADA"


async def test_duplicado_nao_apaga_resultado_original(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await criar_comando(sessoes, "c-3", acao="configurar", vaga=None)
    await g.tratar(*ack("c-3", "aceito"))
    await g.tratar(*ack("c-3", "executado"))
    # O broker reentregou o cmd (QoS 1) e o ESP32 respondeu duplicado
    await g.tratar(*ack("c-3", "rejeitado", erro="COMANDO_DUPLICADO"))
    cmd = await comando(sessoes, "c-3")
    assert cmd.status == "executado" and cmd.erro is None


async def test_cancelar_confirmado_quando_vaga_volta_ao_sensor(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await g.tratar(*estado(1, "reservada", "2026-11-12T10:00:00Z", 80.0))
    await criar_comando(sessoes, "c-4", acao="cancelar_reserva")
    await g.tratar(*ack("c-4", "aceito"))
    await g.tratar(*estado(1, "livre", "2026-11-12T10:01:00Z", 80.0))
    await g.tratar(*ack("c-4", "executado"))
    assert (await comando(sessoes, "c-4")).status == "confirmado"


async def test_ack_de_comando_desconhecido_e_ignorado(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await g.tratar(*ack("c-nao-existe", "aceito"))
    assert await contar(sessoes, m.Comando) == 0
