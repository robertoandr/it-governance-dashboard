"""Testes de itgov/services/dns_check_service.py — SPF, DMARC e DKIM (KPI-EMAIL-02)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import dns.resolver
import pytest

from itgov.services import dns_check_service as dcs


def _resolver(respostas: dict[str, list[str]]) -> MagicMock:
    """Resolver falso: nome consultado -> registros TXT; nome ausente -> NXDOMAIN."""

    def resolve(nome: str, tipo: str) -> list[str]:
        assert tipo == "TXT"
        if nome not in respostas:
            raise dns.resolver.NXDOMAIN
        return [f'"{r}"' for r in respostas[nome]]

    resolver = MagicMock()
    resolver.resolve.side_effect = resolve
    return resolver


@pytest.fixture
def dns_falso():
    def _instalar(respostas: dict[str, list[str]]):
        return patch("dns.resolver.Resolver", return_value=_resolver(respostas))

    return _instalar


def test_get_domain_le_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("M365_TENANT_DOMAIN", "  empresa.com.br ")
    assert dcs._get_domain() == "empresa.com.br"


def test_spf_encontrado_ignora_outros_txt(dns_falso) -> None:
    with dns_falso(
        {"empresa.com.br": ["google-site-verification=x", "v=spf1 include:spf.protection.outlook.com -all"]}
    ):
        assert dcs.check_spf("empresa.com.br") == {
            "found": True,
            "record": "v=spf1 include:spf.protection.outlook.com -all",
        }


def test_spf_ausente(dns_falso) -> None:
    with dns_falso({"empresa.com.br": ["ms=123"]}):
        assert dcs.check_spf("empresa.com.br") == {"found": False, "record": None}


def test_spf_falha_de_dns(dns_falso) -> None:
    with dns_falso({}):
        assert dcs.check_spf("empresa.com.br") == {"found": False, "record": None}


def test_dmarc_extrai_politica(dns_falso) -> None:
    with dns_falso({"_dmarc.empresa.com.br": ["v=DMARC1; p=quarantine; rua=mailto:d@empresa.com.br"]}):
        resultado = dcs.check_dmarc("empresa.com.br")
    assert resultado["found"] is True
    assert resultado["policy"] == "quarantine"


def test_dmarc_sem_politica(dns_falso) -> None:
    with dns_falso({"_dmarc.empresa.com.br": ["v=DMARC1"]}):
        assert dcs.check_dmarc("empresa.com.br")["policy"] is None


def test_dmarc_ausente_e_falha(dns_falso) -> None:
    with dns_falso({"_dmarc.empresa.com.br": ["outra coisa"]}):
        assert dcs.check_dmarc("empresa.com.br")["found"] is False
    with dns_falso({}):
        assert dcs.check_dmarc("empresa.com.br") == {"found": False, "policy": None, "record": None}


def test_dkim_encontrado_vazio_e_falha(dns_falso) -> None:
    with dns_falso({"selector1._domainkey.empresa.com.br": ["v=DKIM1; k=rsa; p=abc"]}):
        assert dcs.check_dkim("empresa.com.br") == {"found": True, "selector": "selector1"}
    with dns_falso({"selector2._domainkey.empresa.com.br": [""]}):
        assert dcs.check_dkim("empresa.com.br", "selector2") == {"found": False, "selector": "selector2"}
    with dns_falso({}):
        assert dcs.check_dkim("empresa.com.br")["found"] is False


def test_summary_sem_dominio() -> None:
    assert dcs.get_email_security_summary("") == {
        "domain": None,
        "available": False,
        "spf": None,
        "dmarc": None,
        "dkim": None,
    }


def test_summary_consolida(dns_falso) -> None:
    with dns_falso(
        {
            "empresa.com.br": ["v=spf1 -all"],
            "_dmarc.empresa.com.br": ["v=DMARC1; p=reject"],
        }
    ):
        resultado = dcs.get_email_security_summary("empresa.com.br")
    assert resultado["available"] is True
    assert resultado["spf"]["found"] is True
    assert resultado["dmarc"]["policy"] == "reject"
    assert resultado["dkim"]["found"] is False
