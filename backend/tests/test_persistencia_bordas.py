"""Assinante de persistência: casos de borda (retidos, duplicatas, ordem dos acks, ts nulo)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from comum import modelos as m
from persistencia.gravador import Relogios
from tests.conftest import BASE, DEVICE, ack, carga, estado, rfid, status_online
from tests.test_persistencia import RECEBIDO, comando, contar, criar_comando, gravador

OUTRO = "esp32-garagem-aabbccddeeff"


def naive(ts):
    return ts.replace(tzinfo=None)


# ----------------------------------------------------------------------
# O que é gravado e o que é ignorado
# ----------------------------------------------------------------------


async def test_topico_cmd_e_ignorado(sessoes):
    """O cmd é publicado pelo operador, que já registrou o comando."""
    await gravador(sessoes).tratar(
        f"{BASE}/cmd", carga(v=1, commandId="c-1", ts="2026-11-12T10:00:00Z", acao="reservar", vaga=1)
    )
    assert await contar(sessoes, m.MensagemInvalida) == 0
    assert await contar(sessoes, m.Dispositivo) == 0


async def test_retido_nao_conta_como_contato(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online(), retido=True)
    async with sessoes() as s:
        d = await s.get(m.Dispositivo, DEVICE)
    assert d.online and d.ultimo_contato is None
    assert await contar(sessoes, m.EventoStatus) == 0  # status retido é antigo, não é evento

    await g.tratar(*estado(1, "livre", "2026-11-12T10:00:00Z", 80.0))
    async with sessoes() as s:
        d = await s.get(m.Dispositivo, DEVICE)
    assert naive(d.ultimo_contato) == naive(RECEBIDO)


async def test_payload_binario_e_longo_e_guardado_como_texto(sessoes):
    await gravador(sessoes).tratar(f"{BASE}/rfid", b"\xff\xfe" + b"x" * 10_000)
    async with sessoes() as s:
        inv = await s.scalar(select(m.MensagemInvalida))
    assert len(inv.payload) <= 4000 and "�" in inv.payload


async def test_estado_erro_grava_distancia_nula(sessoes):
    await gravador(sessoes).tratar(*estado(2, "erro", "2026-11-12T10:00:00Z", dist=None))
    async with sessoes() as s:
        e = await s.scalar(select(m.EstadoVaga))
    assert e.estado == "erro" and e.distancia_cm is None


async def test_dispositivos_nao_se_misturam(sessoes):
    g = gravador(sessoes)
    await g.tratar(*estado(1, "ocupada", "2026-11-12T10:00:00Z"))
    await g.tratar(
        f"garagem/v1/{OUTRO}/vaga/1/estado",
        carga(v=1, deviceId=OUTRO, ts="2026-11-12T10:00:00Z", vaga=1, estado="ocupada",
              distancia={"valor": 9.0, "unidade": "cm"}),
    )
    assert await contar(sessoes, m.Dispositivo) == 2
    assert await contar(sessoes, m.EstadoVaga) == 2  # mesma vaga e ts, dispositivos diferentes


async def test_primeira_mensagem_de_dispositivo_novo_cria_offline(sessoes):
    await gravador(sessoes).tratar(*rfid(1, "04A1B2C3", "2026-11-12T10:00:07Z"))
    async with sessoes() as s:
        d = await s.get(m.Dispositivo, DEVICE)
    assert d is not None and not d.online


async def test_vagas_e_firmware_atualizados_pelo_status(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await g.tratar(
        f"{BASE}/status",
        carga(v=1, deviceId=DEVICE, ts="2026-11-12T11:00:00Z", status="online", firmware="0.11.0", vagas=[1]),
    )
    async with sessoes() as s:
        d = await s.get(m.Dispositivo, DEVICE)
    assert d.firmware == "0.11.0" and d.vagas == [1]
    assert await contar(sessoes, m.EventoStatus) == 2


# ----------------------------------------------------------------------
# Ciclo do comando (R7)
# ----------------------------------------------------------------------


async def test_estado_retido_nao_confirma_comando(sessoes):
    """Estado reentregue ao reconectar não é a resposta ao comando."""
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await criar_comando(sessoes, "c-5")
    await g.tratar(*ack("c-5", "aceito"))
    await g.tratar(*estado(1, "reservada", "2026-11-12T09:00:00Z", 80.0), retido=True)
    assert (await comando(sessoes, "c-5")).confirmado_em is None


async def test_executado_antes_do_estado_tambem_confirma(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await g.tratar(*estado(1, "livre", "2026-11-12T10:00:00Z", 80.0))
    await criar_comando(sessoes, "c-6")
    await g.tratar(*ack("c-6", "aceito"))
    await g.tratar(*ack("c-6", "executado"))
    assert (await comando(sessoes, "c-6")).status == "executado"
    await g.tratar(*estado(1, "reservada", "2026-11-12T10:01:00Z", 80.0))
    assert (await comando(sessoes, "c-6")).status == "confirmado"


async def test_configurar_termina_em_executado(sessoes):
    """configurar não tem estado que o confirme."""
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await criar_comando(sessoes, "c-7", acao="configurar", vaga=None)
    await g.tratar(*ack("c-7", "aceito"))
    await g.tratar(*ack("c-7", "executado"))
    await g.tratar(*estado(1, "livre", "2026-11-12T10:02:00Z", 80.0))
    cmd = await comando(sessoes, "c-7")
    assert cmd.status == "executado" and cmd.confirmado_em is None


async def test_ack_de_outro_dispositivo_nao_altera_o_comando(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await criar_comando(sessoes, "c-8")
    await g.tratar(
        f"garagem/v1/{OUTRO}/ack",
        carga(v=1, deviceId=OUTRO, ts="2026-11-12T10:01:00Z", commandId="c-8", resultado="aceito"),
    )
    assert (await comando(sessoes, "c-8")).status == "enviado"


async def test_reserva_de_outra_vaga_nao_e_confirmada(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await criar_comando(sessoes, "c-9", vaga=1)
    await g.tratar(*ack("c-9", "aceito"))
    await g.tratar(*estado(2, "reservada", "2026-11-12T10:01:00Z", 80.0))
    assert (await comando(sessoes, "c-9")).confirmado_em is None


async def test_ack_repetido_nao_muda_os_horarios(sessoes):
    g = gravador(sessoes)
    await g.tratar(*status_online())
    await criar_comando(sessoes, "c-10")
    await g.tratar(*ack("c-10", "aceito"))
    primeiro = (await comando(sessoes, "c-10")).recebido_em
    g._agora = lambda: RECEBIDO + timedelta(minutes=1)
    await g.tratar(*ack("c-10", "aceito"))
    assert (await comando(sessoes, "c-10")).recebido_em == primeiro


async def test_ack_sem_command_id_nao_quebra(sessoes):
    await gravador(sessoes).tratar(
        f"{BASE}/ack",
        carga(v=1, deviceId=DEVICE, ts="2026-11-12T10:01:00Z", commandId=None, resultado="rejeitado",
              erro="PAYLOAD_INVALIDO"),
    )
    assert await contar(sessoes, m.MensagemInvalida) == 0
    assert await contar(sessoes, m.Dispositivo) == 1


# ----------------------------------------------------------------------
# Horário estimado ("ts": null)
# ----------------------------------------------------------------------


async def test_ts_nulo_sem_referencia_usa_o_recebimento(sessoes):
    await gravador(sessoes).tratar(
        f"{BASE}/rfid",
        carga(v=1, deviceId=DEVICE, ts=None, ts_sincronizado=False, uptime_ms=1000, vaga=1, uid="04A1B2C3"),
    )
    async with sessoes() as s:
        r = await s.scalar(select(m.LeituraRfid))
    assert r.ts_estimado and naive(r.ts) == naive(RECEBIDO)


async def test_ts_nulo_repetido_nao_e_descartado_pelo_indice(sessoes):
    """O índice de duplicatas só vale para ts real: estimativas iguais não somem."""
    g = gravador(sessoes)
    corpo = carga(v=1, deviceId=DEVICE, ts=None, ts_sincronizado=False, uptime_ms=1000, vaga=1, uid="04A1B2C3")
    await g.tratar(f"{BASE}/rfid", corpo)
    await g.tratar(f"{BASE}/rfid", corpo)
    assert await contar(sessoes, m.LeituraRfid) == 2


def test_relogio_nao_estima_horario_no_futuro():
    r = Relogios()
    ref = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
    recebido = ref + timedelta(seconds=5)
    r.registrar(DEVICE, ref, uptime_ms=1000)
    assert r.estimar(DEVICE, 500, recebido) == ref - timedelta(milliseconds=500)
    # Uptime maior que o da referência daria 60 s no futuro: fica o recebimento
    assert r.estimar(DEVICE, 61_000, recebido) == recebido
    assert r.estimar(OUTRO, 500, recebido) == recebido  # sem referência


async def test_reinicio_do_esp32_troca_a_referencia(sessoes):
    g = gravador(sessoes)

    def telemetria(ts, uptime):
        return f"{BASE}/telemetria", carga(
            v=1, deviceId=DEVICE, ts=ts, vagas=[],
            rssi={"valor": -61, "unidade": "dBm"}, uptime={"valor": uptime, "unidade": "ms"},
        )

    await g.tratar(*telemetria("2026-11-12T10:00:00Z", 900_000))
    await g.tratar(*telemetria("2026-11-12T10:04:00Z", 20_000))  # reiniciou
    await g.tratar(
        f"{BASE}/vaga/1/estado",
        carga(v=1, deviceId=DEVICE, ts=None, ts_sincronizado=False, uptime_ms=10_000, vaga=1, estado="livre",
              distancia={"valor": 80.0, "unidade": "cm"}),
    )
    async with sessoes() as s:
        e = await s.scalar(select(m.EstadoVaga))
    assert naive(e.ts) == datetime(2026, 11, 12, 10, 3, 50)
