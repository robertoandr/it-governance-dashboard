"""Consolidação de /directory/subscriptions por SKU (teste e renovação)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import httpx

from itgov.services import m365_assinaturas as ma
from itgov.services.m365_assinaturas import consolidar


def _sub(sku: str, status: str = "Enabled", trial: bool = False, prox: str | None = None) -> dict:
    return {"skuPartNumber": sku, "status": status, "isTrial": trial, "nextLifecycleDateTime": prox}


def test_teste_e_renovacao_por_sku() -> None:
    out = consolidar(
        [
            _sub("THREAT_INTELLIGENCE", trial=True, prox="2026-10-04T00:00:00Z"),
            _sub("O365_BUSINESS_ESSENTIALS", prox="2027-07-22T00:00:00Z"),
            _sub("O365_BUSINESS_ESSENTIALS", prox="2027-02-03T00:00:00Z"),
        ]
    )
    assert out["THREAT_INTELLIGENCE"].teste is True
    assert out["THREAT_INTELLIGENCE"].renovacao == date(2026, 10, 4)
    assert out["O365_BUSINESS_ESSENTIALS"].teste is False
    assert out["O365_BUSINESS_ESSENTIALS"].renovacao == date(2027, 2, 3)  # a mais próxima
    assert out["O365_BUSINESS_ESSENTIALS"].ativas == 2


def test_suspensa_nao_conta_e_sem_vencimento_vira_none() -> None:
    out = consolidar(
        [
            _sub("PROJECT_P1", status="Suspended", prox="2026-11-16T00:00:00Z"),
            _sub("PROJECT_P1", prox="2027-02-02T00:00:00Z"),
            _sub("MICROSOFT_365_COPILOT_BUSINESS_DEPT", prox="9999-12-31T00:00:00Z"),
        ]
    )
    assert out["PROJECT_P1"].renovacao == date(2027, 2, 2)
    assert out["PROJECT_P1"].suspensas == 1
    assert out["MICROSOFT_365_COPILOT_BUSINESS_DEPT"].renovacao is None


def test_pago_e_teste_juntos_nao_e_teste() -> None:
    out = consolidar([_sub("SPB", trial=True), _sub("SPB")])
    assert out["SPB"].teste is False


def test_graph_fora_do_ar_devolve_vazio() -> None:
    async def falha() -> list[dict]:
        raise httpx.ConnectError("sem rede")

    with patch.object(ma, "_buscar", falha):
        assert ma._carregar() == {}
