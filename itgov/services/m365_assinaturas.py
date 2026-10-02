"""Assinaturas do tenant M365: o que é teste e quando cada SKU renova.

``subscribedSkus`` (de onde vem o uso das licenças) não diz se a SKU é teste
nem quando vence. ``/directory/subscriptions`` diz — uma linha por assinatura
comprada, com ``isTrial``, ``status`` e ``nextLifecycleDateTime``. Juntamos por
``skuPartNumber`` para a tela de licenças não precisar de renovação digitada.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime

import httpx
import structlog
from pydantic import BaseModel

from itgov.services.graph_client import _fetch_token
from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

_URL = "https://graph.microsoft.com/v1.0/directory/subscriptions"
_ATIVAS = {"Enabled", "Warning"}


class AssinaturaSku(BaseModel):
    """Situação consolidada das assinaturas de uma SKU."""

    sku_name: str
    teste: bool
    renovacao: date | None
    ativas: int
    suspensas: int


def _data(valor: str | None) -> date | None:
    """Data de ``nextLifecycleDateTime``; 9999-12-31 (sem vencimento) vira None."""
    if not valor:
        return None
    try:
        d = datetime.fromisoformat(valor.replace("Z", "+00:00")).date()
    except ValueError:
        return None
    return None if d.year >= 9999 else d


def consolidar(assinaturas: list[dict]) -> dict[str, AssinaturaSku]:
    """Agrupa as assinaturas por SKU.

    Teste = todas as assinaturas ativas da SKU são de teste. Renovação = a
    próxima data entre as ativas (a primeira que vai cobrar ou vencer).
    """
    por_sku: dict[str, list[dict]] = {}
    for a in assinaturas:
        sku = a.get("skuPartNumber")
        if sku:
            por_sku.setdefault(sku, []).append(a)

    out: dict[str, AssinaturaSku] = {}
    for sku, lista in por_sku.items():
        ativas = [a for a in lista if a.get("status") in _ATIVAS]
        datas = [d for d in (_data(a.get("nextLifecycleDateTime")) for a in ativas) if d]
        out[sku] = AssinaturaSku(
            sku_name=sku,
            teste=bool(ativas) and all(a.get("isTrial") for a in ativas),
            renovacao=min(datas) if datas else None,
            ativas=len(ativas),
            suspensas=sum(1 for a in lista if a.get("status") == "Suspended"),
        )
    return out


async def _buscar() -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _fetch_token(client)
        resp = await client.get(_URL, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return list(resp.json().get("value", []))


def _carregar() -> dict[str, AssinaturaSku]:
    try:
        return consolidar(asyncio.run(_buscar()))
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
        log.warning("m365_assinaturas.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return {}


_cache: CacheSWR[dict[str, AssinaturaSku]] = CacheSWR("m365.assinaturas", ttl=3600, valido=bool)


def obter_assinaturas() -> dict[str, AssinaturaSku]:
    """Assinaturas por SKU (cache de 1 h; ``{}`` se o Graph estiver fora)."""
    return _cache.get(_carregar)
