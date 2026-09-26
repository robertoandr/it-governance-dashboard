"""Testes do registro no Zabbix em collectors/network_discovery.py.

Regressões cobertas:
- IDs fixos de grupos/templates apontavam para objetos errados após a
  reinstalação do Zabbix (groupid 22 virou "Certificados SSL") — agora tudo
  é resolvido por nome.
- host.update substituía grupos/templates de hosts existentes (inclusive os
  configurados à mão) — agora só soma.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import collectors.network_discovery as nd


def _api(grupos: dict[str, str], templates: dict[str, str]) -> MagicMock:
    api = MagicMock()
    api.hostgroup.get.side_effect = lambda output, filter: (
        [{"groupid": grupos[filter["name"]]}] if filter["name"] in grupos else []
    )
    api.hostgroup.create.return_value = {"groupids": ["99"]}
    api.template.get.side_effect = lambda output, filter: (
        [{"templateid": templates[filter["host"]]}] if filter["host"] in templates else []
    )
    return api


def _host(ip: str = "172.29.0.10", groups: list[str] | None = None, templates: list[str] | None = None):
    h = nd.DiscoveredHost(ip=ip)
    h.groups = groups if groups is not None else [nd.GRP_FIREWALL]
    h.templates = templates if templates is not None else [nd.TMPL_ICMP_PING]
    return h


class TestZbxIds:
    def test_resolve_grupo_por_nome(self) -> None:
        api = _api({"Firewall": "27"}, {})
        assert nd._ZbxIds(api).grupo(nd.GRP_FIREWALL) == "27"

    def test_cria_grupo_ausente(self) -> None:
        api = _api({}, {})
        assert nd._ZbxIds(api).grupo(nd.GRP_SERVIDORES) == "99"
        api.hostgroup.create.assert_called_once_with(name="Servidores")

    def test_template_ausente_retorna_none(self) -> None:
        assert nd._ZbxIds(_api({}, {})).template("Nao Existe") is None

    def test_cache_evita_consultas_repetidas(self) -> None:
        api = _api({"Firewall": "27"}, {"ICMP Ping": "10564"})
        ids = nd._ZbxIds(api)
        for _ in range(3):
            ids.grupo(nd.GRP_FIREWALL)
            ids.template(nd.TMPL_ICMP_PING)
        assert api.hostgroup.get.call_count == 1
        assert api.template.get.call_count == 1


class TestRegistrarNoZabbix:
    @pytest.fixture
    def api(self) -> MagicMock:
        return _api({"Firewall": "27"}, {"ICMP Ping": "10564"})

    def test_cria_host_com_ids_resolvidos(self, api: MagicMock) -> None:
        resultado = nd._registrar_no_zabbix(_host(), api, {}, nd._ZbxIds(api))
        assert resultado == "created"
        kwargs = api.host.create.call_args.kwargs
        assert kwargs["groups"] == [{"groupid": "27"}]
        assert kwargs["templates"] == [{"templateid": "10564"}]

    def test_template_ausente_nao_quebra_criacao(self, api: MagicMock) -> None:
        host = _host(templates=[nd.TMPL_ICMP_PING, "Nao Existe"])
        nd._registrar_no_zabbix(host, api, {}, nd._ZbxIds(api))
        assert api.host.create.call_args.kwargs["templates"] == [{"templateid": "10564"}]

    def test_host_existente_so_ganha_vinculos(self, api: MagicMock) -> None:
        existing = {"172.29.0.10": {"hostid": "500", "groupids": {"4"}, "templateids": {"10001"}}}
        resultado = nd._registrar_no_zabbix(_host(), api, existing, nd._ZbxIds(api))
        assert resultado == "updated"
        kwargs = api.host.update.call_args.kwargs
        assert kwargs["hostid"] == "500"
        assert kwargs["groups"] == [{"groupid": "27"}, {"groupid": "4"}]
        assert kwargs["templates"] == [{"templateid": "10001"}, {"templateid": "10564"}]

    def test_host_existente_ja_completo_nao_e_alterado(self, api: MagicMock) -> None:
        existing = {"172.29.0.10": {"hostid": "500", "groupids": {"27"}, "templateids": {"10564"}}}
        assert nd._registrar_no_zabbix(_host(), api, existing, nd._ZbxIds(api)) == "skipped"
        api.host.update.assert_not_called()

    def test_erro_ao_resolver_grupo_fica_restrito_ao_host(self) -> None:
        from zabbix_utils.exceptions import APIRequestError

        api = _api({}, {"ICMP Ping": "10564"})
        api.hostgroup.create.side_effect = APIRequestError("No permissions")
        assert nd._registrar_no_zabbix(_host(), api, {}, nd._ZbxIds(api)) == "skipped"
        api.host.create.assert_not_called()
