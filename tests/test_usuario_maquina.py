"""Testes do último usuário logado por máquina (itgov/services/usuario_maquina.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from itgov.services import usuario_maquina
from itgov.services.acronis_inventario import Computador
from itgov.services.m365_pessoas import Pessoa, PessoasM365


def _pc(nome: str, usuario: str = "", ips: list[str] | None = None) -> Computador:
    return Computador(
        nome=nome,
        tipo="desktop",
        usuario=usuario,
        ips=ips or [],
        usuario_em=datetime(2026, 11, 19, 12, 0, tzinfo=UTC),
    )


_PESSOAS = PessoasM365(
    por_login={
        "fulano.diretor": Pessoa(nome="Fulano Diretor", email="fulano@x.com", ativa=True),
        "saiu.daqui": Pessoa(nome="Saiu Daqui", email="saiu@x.com", ativa=False),
    }
)


def test_nome_curto_ignora_dominio_e_caixa() -> None:
    assert usuario_maquina.nome_curto("DESKTOP-3H767H5.grupogadens.com.br") == "desktop-3h767h5"
    assert usuario_maquina.nome_curto("") == ""


def test_acha_pelo_fqdn_e_cruza_com_m365() -> None:
    buscar = usuario_maquina.montar_busca([_pc("NT1007261", "Fulano.Diretor (GRUPOGADENS)")], _PESSOAS)
    u = buscar("NT1007261.grupogadens.com.br", "")
    assert u is not None
    assert (u.login, u.nome, u.email, u.ativa) == (
        "Fulano.Diretor (GRUPOGADENS)",
        "Fulano Diretor",
        "fulano@x.com",
        True,
    )
    assert u.rotulo == "Fulano Diretor"
    assert u.em == "2026-11-19T12:00:00+00:00"


def test_acha_pelo_ip_quando_nome_nao_bate() -> None:
    buscar = usuario_maquina.montar_busca([_pc("PC-A", "saiu.daqui", ips=["172.29.4.10"])], _PESSOAS)
    assert buscar("172.29.4.10", "").ativa is False  # alvo digitado como IP
    assert buscar("outro-nome", "172.29.4.10").login == "saiu.daqui"  # IP do registro


def test_sem_conta_m365_usa_login_como_rotulo() -> None:
    buscar = usuario_maquina.montar_busca([_pc("PC-B", "DESKTOP-X\\Admin")], None)
    u = buscar("pc-b", "")
    assert u is not None and u.nome == "" and u.ativa is None
    assert u.rotulo == "DESKTOP-X\\Admin"


def test_maquina_sem_usuario_ou_desconhecida_devolve_none() -> None:
    buscar = usuario_maquina.montar_busca([_pc("PC-C", "")], _PESSOAS)
    assert buscar("PC-C", "") is None
    assert buscar("NAO-EXISTE", "") is None
    assert buscar("", "") is None


def test_anotar_acrescenta_usuario_em_cada_item() -> None:
    buscar = usuario_maquina.montar_busca([_pc("PC-D", "fulano.diretor")], _PESSOAS)
    itens = usuario_maquina.anotar([{"maquina": "PC-D.grupogadens.com.br"}, {"maquina": "X", "ip": ""}], buscar)
    assert itens[0]["usuario"]["nome"] == "Fulano Diretor"
    assert itens[1]["usuario"] is None


def test_busca_atual_sem_inventario_nao_quebra() -> None:
    with (
        patch.object(usuario_maquina.acronis_inventario, "obter_inventario", return_value=None),
        patch.object(usuario_maquina.m365_pessoas, "obter", return_value=None),
    ):
        assert usuario_maquina.busca_atual()("PC-E", "") is None
