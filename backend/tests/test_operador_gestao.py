"""Operador: usuários, tags (R16), alertas, limite de login, segredo, CORS e páginas."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from comum import modelos as m
from operador import app as modulo_app
from operador import auth
from operador.app import criar_app
from persistencia.gravador import Gravador
from tests.conftest import DEVICE, rfid, status_online
from tests.test_operador import SEGREDO, PublicadorFalso, entrar

# Fixtures cliente/publicador vêm de test_operador
from tests.test_operador import cliente, publicador  # noqa: F401


# ----------------------------------------------------------------------
# Usuários
# ----------------------------------------------------------------------


async def test_trocar_a_propria_senha(cliente):
    op = await entrar(cliente, "op", "senha-operador")
    r = await cliente.post("/api/eu/senha", headers=op, json={"senha_atual": "errada", "senha_nova": "nova-senha-1"})
    assert r.status_code == 403
    r = await cliente.post("/api/eu/senha", headers=op, json={"senha_atual": "senha-operador", "senha_nova": "curta"})
    assert r.status_code == 422
    r = await cliente.post("/api/eu/senha", headers=op, json={"senha_atual": "senha-operador", "senha_nova": "nova-senha-1"})
    assert r.status_code == 204
    assert (await cliente.post("/api/login", json={"usuario": "op", "senha": "senha-operador"})).status_code == 401
    await entrar(cliente, "op", "nova-senha-1")


async def test_listar_e_apagar_usuarios(cliente):
    admin = await entrar(cliente, "admin", "senha-admin")
    op = await entrar(cliente, "op", "senha-operador")
    assert (await cliente.get("/api/usuarios", headers=op)).status_code == 403
    nomes = [u["nome"] for u in (await cliente.get("/api/usuarios", headers=admin)).json()]
    assert nomes == ["admin", "op"]

    assert (await cliente.delete("/api/usuarios/admin", headers=admin)).status_code == 409  # o próprio
    assert (await cliente.delete("/api/usuarios/ninguem", headers=admin)).status_code == 404
    assert (await cliente.delete("/api/usuarios/op", headers=admin)).status_code == 204
    # O token de quem foi apagado para de valer na hora
    assert (await cliente.get("/api/eu", headers=op)).status_code == 401


async def test_perfil_alterado_vale_sem_novo_login(cliente, sessoes):
    from sqlalchemy import update

    op = await entrar(cliente, "op", "senha-operador")
    async with sessoes() as s, s.begin():
        await s.execute(update(m.Usuario).where(m.Usuario.nome == "op").values(perfil="administrador"))
    assert (await cliente.get("/api/eu", headers=op)).json()["perfil"] == "administrador"


# ----------------------------------------------------------------------
# Tags
# ----------------------------------------------------------------------


async def test_cadastro_de_tags(cliente, sessoes):
    admin = await entrar(cliente, "admin", "senha-admin")
    op = await entrar(cliente, "op", "senha-operador")
    nova = {"uid": "04a1b2c3", "apelido": "Carro do João", "dono": "João"}
    assert (await cliente.post("/api/tags", headers=op, json=nova)).status_code == 403
    r = await cliente.post("/api/tags", headers=admin, json=nova)
    assert r.status_code == 201 and r.json()["uid"] == "04A1B2C3"
    assert (await cliente.post("/api/tags", headers=admin, json={**nova, "uid": "04A1B2C3"})).status_code == 409
    assert (await cliente.post("/api/tags", headers=admin, json={**nova, "uid": "04:A1"})).status_code == 422
    assert [t["apelido"] for t in (await cliente.get("/api/tags", headers=op)).json()] == ["Carro do João"]

    # A leitura da tag aparece com o apelido
    g = Gravador(sessoes)
    await g.tratar(*status_online())
    await g.tratar(*rfid(1, "04A1B2C3", "2026-11-12T10:00:07Z"))
    await g.tratar(*rfid(2, "0A0B0C0D", "2026-11-12T10:00:08Z"))
    leituras = {r["uid"]: r["apelido"] for r in (await cliente.get("/api/rfid", headers=op)).json()}
    assert leituras == {"04A1B2C3": "Carro do João", "0A0B0C0D": None}

    assert (await cliente.delete("/api/tags/04a1b2c3", headers=admin)).status_code == 204
    assert (await cliente.delete("/api/tags/04a1b2c3", headers=admin)).status_code == 404


# ----------------------------------------------------------------------
# Alertas
# ----------------------------------------------------------------------


async def test_resolver_alerta(cliente, sessoes):
    await Gravador(sessoes).tratar(*status_online())
    async with sessoes() as s, s.begin():
        s.add(m.Alerta(device_id=DEVICE, vaga=1, tipo="reserva_expirada", chave="reserva:c-1",
                       mensagem="expirou", criado_em=datetime.now(timezone.utc)))
    op = await entrar(cliente, "op", "senha-operador")
    [alerta] = (await cliente.get("/api/alertas", headers=op)).json()
    r = await cliente.post(f"/api/alertas/{alerta['id']}/resolver", headers=op)
    assert r.status_code == 200 and r.json()["resolvido_em"]
    assert (await cliente.get("/api/alertas", headers=op)).json() == []
    assert (await cliente.post("/api/alertas/999/resolver", headers=op)).status_code == 404


# ----------------------------------------------------------------------
# Segurança
# ----------------------------------------------------------------------


async def test_login_bloqueado_apos_5_erros(cliente):
    for _ in range(5):
        r = await cliente.post("/api/login", json={"usuario": "admin", "senha": "chute"})
        assert r.status_code == 401
    # Bloqueado mesmo com a senha certa
    assert (await cliente.post("/api/login", json={"usuario": "admin", "senha": "senha-admin"})).status_code == 429
    # Outro usuário segue entrando
    await entrar(cliente, "op", "senha-operador")


def test_bloqueio_do_login_passa_com_o_tempo():
    limite = auth.LimiteTentativas(maximo=2, janela=timedelta(minutes=5))
    t = datetime(2026, 11, 14, tzinfo=timezone.utc)
    limite.registrar_erro("1.2.3.4", "admin", t)
    limite.registrar_erro("1.2.3.4", "admin", t)
    assert limite.bloqueado("1.2.3.4", "admin", t + timedelta(minutes=4))
    assert not limite.bloqueado("5.6.7.8", "admin", t)
    assert not limite.bloqueado("1.2.3.4", "admin", t + timedelta(minutes=5))


@pytest.mark.parametrize("segredo", ["troque-este-segredo", "curto", "troque" + "x" * 40])
def test_segredo_fraco_e_recusado(segredo):
    with pytest.raises(RuntimeError, match="JWT_SEGREDO"):
        auth.validar_segredo(segredo)


def test_segredo_forte_e_aceito():
    forte = "Zq3" * 16
    assert auth.validar_segredo(forte) == forte


def test_operador_nao_sobe_com_o_segredo_do_exemplo(monkeypatch):
    monkeypatch.setenv("JWT_SEGREDO", "troque-este-segredo")
    monkeypatch.delenv("ADMIN_USUARIO", raising=False)
    app = criar_app(object(), publicar=PublicadorFalso(), iniciar_mqtt=False)
    with pytest.raises(RuntimeError, match="JWT_SEGREDO"):
        with TestClient(app):
            pass


# ----------------------------------------------------------------------
# Como as páginas chegam ao navegador
# ----------------------------------------------------------------------


def test_cors_liberado_so_para_as_origens_configuradas(monkeypatch):
    monkeypatch.setenv("CORS_ORIGENS", "http://painel.local:5500")
    app = criar_app(object(), jwt_segredo=SEGREDO, publicar=PublicadorFalso(), iniciar_mqtt=False)
    pedido = {"Access-Control-Request-Method": "GET"}
    with TestClient(app) as c:
        ok = c.options("/api/vagas", headers={**pedido, "Origin": "http://painel.local:5500"})
        assert ok.headers.get("access-control-allow-origin") == "http://painel.local:5500"
        outro = c.options("/api/vagas", headers={**pedido, "Origin": "http://intruso.com"})
        assert "access-control-allow-origin" not in outro.headers


def test_sem_cors_configurado_nao_libera(monkeypatch):
    monkeypatch.delenv("CORS_ORIGENS", raising=False)
    app = criar_app(object(), jwt_segredo=SEGREDO, publicar=PublicadorFalso(), iniciar_mqtt=False)
    with TestClient(app) as c:
        r = c.options("/api/vagas", headers={"Origin": "http://x", "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in r.headers


def test_pasta_paginas_e_servida_na_raiz_sem_esconder_a_api(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<h1>Painel</h1>", encoding="utf-8")
    monkeypatch.setattr(modulo_app, "PASTA_PAGINAS", tmp_path)
    app = criar_app(object(), jwt_segredo=SEGREDO, publicar=PublicadorFalso(), iniciar_mqtt=False)
    with TestClient(app) as c:
        assert "Painel" in c.get("/").text
        assert c.get("/saude").json()["servico"] == "operador"
        assert c.get("/docs").status_code == 200
