"""Lista de permissões/bloqueios dos planos do Acronis, categorias dos incidentes e as telas."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from itgov.services import acronis_edr
from itgov.services import acronis_permissoes as ap

# Formato real (05/11): módulos do plano com listas + <Lista>Details
_POLITICAS = [
    {
        "policy": [
            {"type": "policy.protection.total", "name": "Complete protection"},
            {
                "type": "policy.security.antimalware_protection",
                "settings": {
                    "Exclusions": {
                        "TrustedProcesses": [r"C:\WINDOWS\system32\cmd.exe", r"C:\App\ok.exe"],
                        "TrustedProcessesDetails": [
                            {"Description": "Added for incident [913]", "Values": [r"C:\WINDOWS\system32\cmd.exe"]}
                        ],
                        "TrustedHashesDetails": [
                            {"Description": "Adicionado da Quarentena 11 de set. de 2026", "Values": ["521318EF2BF6"]}
                        ],
                        "NotMonitoredFilesAndFolders": [r"C:\Users\usuario\Downloads"],
                        "BlockedProcesses": [],
                        "BlockedHashesDetails": None,
                    }
                },
            },
            {
                "type": "policy.security.active_protection",
                "settings": {
                    "ProcessBlacklist": [r"C:\$Recycle.Bin\S-1\$RK3KY4Y.exe"],
                    "ProcessBlacklistDetails": [
                        {"Description": "Added for incident [212]", "Values": [r"C:\$Recycle.Bin\S-1\$RK3KY4Y.exe"]}
                    ],
                    "ConnectionWhitelist": [],
                },
            },
            {"type": "policy.management.agent", "settings": {"TrustedProcesses": ["ignorado.exe"]}},
        ]
    },
    {"policy": [{"type": "policy.security.antimalware_protection", "settings": {}}]},
]


@pytest.mark.parametrize(
    ("descricao", "esperado"),
    [
        ("Added for incident [913]", ("incidente", 913)),
        ("Adicionado da Quarentena 11 de set. de 2026", ("quarentena", None)),
        ("Restored from quarantine", ("quarentena", None)),
        ("liberado pelo Roberto", ("manual", None)),
        ("", ("manual", None)),
    ],
)
def test_origem_da_descricao(descricao: str, esperado: tuple) -> None:
    assert ap.origem_da_descricao(descricao) == esperado


def test_itens_das_politicas() -> None:
    itens, planos = ap.itens_das_politicas(_POLITICAS)
    assert planos == ["Complete protection"]  # plano sem nome e sem itens fica de fora
    por_valor = {i.valor: i for i in itens}
    assert len(itens) == 5  # cmd.exe aparece uma vez só (Details + lista simples)
    cmd = por_valor[r"C:\WINDOWS\system32\cmd.exe"]
    assert (cmd.lista, cmd.permitido, cmd.origem, cmd.incidente) == ("Processo confiável", True, "incidente", 913)
    assert cmd.risco.startswith("binário do sistema")
    assert por_valor["521318EF2BF6"].origem == "quarentena" and por_valor["521318EF2BF6"].risco == ""
    assert por_valor[r"C:\App\ok.exe"].origem == "manual" and por_valor[r"C:\App\ok.exe"].risco == ""
    assert por_valor[r"C:\Users\usuario\Downloads"].risco == "pasta inteira de usuário fora da proteção"
    bloq = por_valor[r"C:\$Recycle.Bin\S-1\$RK3KY4Y.exe"]
    assert (bloq.permitido, bloq.lista, bloq.risco) == (False, "Processo bloqueado (proteção ativa)", "")
    assert "ignorado.exe" not in por_valor  # só módulos policy.security.*


def test_painel_separa_permitidos_bloqueados_e_arriscados() -> None:
    itens, planos = ap.itens_das_politicas(_POLITICAS)
    p = ap.PainelPermissoes(atualizado_em=datetime.now(UTC), itens=itens, planos=planos)
    assert (len(p.permitidos), len(p.bloqueados), len(p.arriscados)) == (4, 1, 2)


def test_contar_categorias() -> None:
    inc = [
        acronis_edr.IncidenteEdr(incident_id="1", customer_id="c", categorias=["URL_BLOCKED", "WEBSITE_URL_BLOCKED"]),
        acronis_edr.IncidenteEdr(incident_id="2", customer_id="c", categorias=["URL_BLOCKED"]),
        acronis_edr.IncidenteEdr(incident_id="3", customer_id="c", categorias=["NOVA_CATEGORIA"]),
        acronis_edr.IncidenteEdr(incident_id="4", customer_id="c"),
    ]
    assert ap.contar_categorias(inc) == [
        ("URL_BLOCKED", "URL bloqueada", 2),
        ("WEBSITE_URL_BLOCKED", "Site bloqueado", 1),
        ("NOVA_CATEGORIA", "Nova categoria", 1),
        ("SEM_CATEGORIA", "Sem categoria", 1),
    ]


def test_carregar_junta_planos_e_incidentes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "_politicas", lambda: _POLITICAS)
    monkeypatch.setattr(
        acronis_edr,
        "listar_abertos",
        lambda: [
            acronis_edr.IncidenteEdr(incident_id="1", customer_id="c", maquina="PC-1", categorias=["URL_BLOCKED"]),
            acronis_edr.IncidenteEdr(incident_id="2", customer_id="c", maquina="PC-1", categorias=["PROCESS_DETECTED"]),
        ],
    )
    p = ap._carregar()
    assert p is not None
    assert (p.incidentes_abertos, p.maquinas_com_incidente, len(p.categorias)) == (2, 1, 2)


def test_carregar_sem_incidentes_mantem_planos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "_politicas", lambda: _POLITICAS)

    def falha() -> list:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(acronis_edr, "listar_abertos", falha)
    p = ap._carregar()
    assert p is not None and p.erro_incidentes and p.itens and not p.categorias


def test_carregar_falha_nos_planos_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def falha() -> list:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(ap, "_politicas", falha)
    assert ap._carregar() is None


def test_obter_sem_acronis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACRONIS_BASE_URL", raising=False)
    assert ap.obter() is None and ap.carregando() is False


# ── Telas ───────────────────────────────────────────────────────────────────


@pytest.fixture
def com_painel(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    itens, planos = ap.itens_das_politicas(_POLITICAS)
    painel = ap.PainelPermissoes(
        atualizado_em=datetime(2026, 11, 5, 16, 0, tzinfo=UTC),
        itens=itens,
        planos=planos,
        categorias=[("URL_BLOCKED", "URL bloqueada", 208), ("PROCESS_DETECTED", "Processo suspeito", 5)],
        incidentes_abertos=213,
        maquinas_com_incidente=70,
    )
    monkeypatch.setattr(ap, "obter", lambda: painel)
    monkeypatch.setattr(ap, "carregando", lambda: False)


def test_pagina_permissoes(authed_client, com_painel) -> None:
    html = authed_client.get("/gov/cyber/permissoes").get_data(as_text=True)
    assert "aba: 'permissoes'" in html
    assert "Revisar no console" in html and r"C:\WINDOWS\system32\cmd.exe" in html
    assert "incidente 913" in html and "quarentena" in html
    assert "lido 05/11 13:00" in html  # Brasília


def test_pagina_permissoes_filtra_bloqueios(authed_client, com_painel) -> None:
    html = authed_client.get("/gov/cyber/permissoes?ver=bloqueados").get_data(as_text=True)
    tabela = html.split("<tbody", 1)[1]
    assert "RK3KY4Y" in tabela and "521318EF2BF6" not in tabela


def test_noc_mostra_categorias(authed_client, com_painel) -> None:
    html = authed_client.get("/gov/cyber/noc").get_data(as_text=True)
    assert "Incidentes abertos por categoria" in html
    assert "213 incidentes não mitigados em 70 máquinas" in html and "URL bloqueada" in html
