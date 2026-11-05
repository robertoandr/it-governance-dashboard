"""Licenças do Acronis Cyber Protect: uso × contratado por item (item 3.3 do plano).

Lê da API de contas (pública):

- ``/api/2/tenants/{tenant}/offering_items`` — itens ligados e a cota contratada
- ``/api/2/tenants/{tenant}/usages`` — quanto de cada item está em uso

O tenant é o da própria conta de serviço (``/api/2/clients/{client_id}``).
Só entram itens de carga (estações, pacotes avançados); armazenamento fica de
fora. A partir de 85% o item fica em atenção e a partir de 95% em crítico, como
as licenças do Microsoft 365.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

ATENCAO_PCT = 85.0
CRITICO_PCT = 95.0
_TIMEOUT = 30

# Nome na API → nome na tela. Itens fora daqui aparecem com o nome da API.
NOMES: dict[str, str] = {
    "pw_base_workstations": "Estações de trabalho (proteção)",
    "pw_base_secondary_workstations": "Estações secundárias",
    "pw_base_servers": "Servidores (proteção)",
    "pw_base_vms": "Máquinas virtuais (proteção)",
    "pw_pack_adv_management": "Advanced Management (patch e vulnerabilidade)",
    "pw_pack_adv_security": "Advanced Security",
    "pw_pack_adv_security_xdr": "Advanced Security + XDR",
    "pw_pack_adv_dlp": "Advanced DLP",
    "pw_pack_adv_backup": "Advanced Backup",
    "pw_pack_adv_email_security": "Advanced Email Security",
}
# Armazenamento e contadores agregados não são licença por máquina
_FORA = ("storage", "total_", "immutable")


class LicencaAcronis(BaseModel):
    """Um item de licença do Acronis.

    Attributes:
        chave: Nome do item na API (ex.: ``pw_base_workstations``).
        nome: Nome mostrado na tela.
        usado: Quantas cargas usam o item.
        contratado: Cota contratada; ``None`` = sem cota definida.
    """

    chave: str
    nome: str
    usado: int = 0
    contratado: int | None = None

    @property
    def uso_pct(self) -> float | None:
        """Percentual usado da cota (None sem cota)."""
        if not self.contratado:
            return None
        return round(100 * self.usado / self.contratado, 1)

    @property
    def livres(self) -> int | None:
        """Licenças sobrando (negativo = acima da cota; None sem cota)."""
        return None if self.contratado is None else self.contratado - self.usado

    @property
    def nivel(self) -> str:
        """``critico`` (≥95% ou acima da cota), ``atencao`` (≥85%) ou ``ok``."""
        pct = self.uso_pct
        if pct is None:
            return "ok"
        if pct >= CRITICO_PCT:
            return "critico"
        return "atencao" if pct >= ATENCAO_PCT else "ok"


class LicencasAcronis(BaseModel):
    """Licenças lidas do Acronis."""

    atualizado_em: datetime
    itens: list[LicencaAcronis]

    @property
    def em_alerta(self) -> list[LicencaAcronis]:
        """Itens em atenção ou críticos, do mais cheio ao menos cheio."""
        return sorted((i for i in self.itens if i.nivel != "ok"), key=lambda i: -(i.uso_pct or 0))


def _creds() -> tuple[str, str]:
    client_id = os.getenv("ACRONIS_CYBER_CLIENT_ID") or os.getenv("ACRONIS_CLIENT_ID") or ""
    secret = os.getenv("ACRONIS_CYBER_CLIENT_SECRET") or os.getenv("ACRONIS_CLIENT_SECRET") or ""
    return client_id, secret


def configurado() -> bool:
    """Há base e credenciais do Acronis?"""
    client_id, secret = _creds()
    return bool(os.getenv("ACRONIS_BASE_URL") and client_id and secret)


def _cota(item: dict[str, Any]) -> int | None:
    cota = item.get("quota")
    valor = cota.get("value") if isinstance(cota, dict) else cota
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def montar(offering_items: list[dict[str, Any]], usos: list[dict[str, Any]], agora: datetime) -> LicencasAcronis:
    """Junta cota e uso por item.

    Entra o item ligado (``status == 1``) que tem cota ou uso; armazenamento e
    contadores agregados ficam de fora.

    Args:
        offering_items: Itens de ``/offering_items``.
        usos: Itens de ``/usages``.
        agora: Momento da leitura.

    Returns:
        As licenças, na ordem dos nomes conhecidos e depois alfabética.
    """
    usado: dict[str, int] = {}
    for u in usos:
        nome = str(u.get("name") or "")
        try:
            usado[nome] = usado.get(nome, 0) + int(u.get("value") or 0)
        except (TypeError, ValueError):
            continue
    itens: dict[str, LicencaAcronis] = {}
    for o in offering_items:
        nome = str(o.get("name") or "")
        if not nome or o.get("status") != 1 or any(f in nome for f in _FORA):
            continue
        cota = _cota(o)
        em_uso = usado.get(nome, 0)
        if cota is None and not em_uso:
            continue
        itens[nome] = LicencaAcronis(chave=nome, nome=NOMES.get(nome, nome), usado=em_uso, contratado=cota)
    ordem = {k: i for i, k in enumerate(NOMES)}
    return LicencasAcronis(
        atualizado_em=agora,
        itens=sorted(itens.values(), key=lambda i: (ordem.get(i.chave, len(ordem)), i.chave)),
    )


def _json(resp: httpx.Response) -> Any:
    resp.raise_for_status()
    return json.loads(resp.content.decode("utf-8", "replace"))


async def _buscar() -> LicencasAcronis:
    base = os.getenv("ACRONIS_BASE_URL", "").rstrip("/")
    client_id, secret = _creds()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        token = _json(
            await c.post(f"{base}/api/2/idp/token", auth=(client_id, secret), data={"grant_type": "client_credentials"})
        )["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        tenant = _json(await c.get(f"{base}/api/2/clients/{client_id}"))["tenant_id"]
        ofertas = _json(await c.get(f"{base}/api/2/tenants/{tenant}/offering_items", params={"edition": "*"}))
        usos = _json(await c.get(f"{base}/api/2/tenants/{tenant}/usages"))
    return montar(ofertas.get("items", []), usos.get("items", []), datetime.now(UTC))


def _carregar() -> LicencasAcronis | None:
    try:
        return asyncio.run(_buscar())
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as exc:
        log.warning("acronis_licencas.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return None


_cache: CacheSWR[LicencasAcronis | None] = CacheSWR(
    "acronis.licencas", ttl=3600, valido=lambda v: v is not None, ttl_falha=600
)


def obter() -> LicencasAcronis | None:
    """Licenças do Acronis (cache de 1 h; a primeira leitura roda em segundo plano).

    Returns:
        As licenças; None sem Acronis, durante a primeira leitura ou se falhou.
    """
    if not configurado():
        return None
    if not _cache.carregado:
        _cache.aquecer(_carregar)
        return None
    return _cache.get(_carregar)


def carregando() -> bool:
    """A primeira leitura ainda não terminou?"""
    return configurado() and not _cache.carregado
