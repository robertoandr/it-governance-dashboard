"""Inventário de hardware dos computadores pelo agente Acronis (itens zb2/zb3).

O Zabbix não tem agente nos computadores; o Acronis tem, e guarda por máquina
o inventário de hardware (namespace ``hwi``: placa, CPU, discos, RAM) e os
dados do agente (SO, memória, IPs). Uma chamada por máquina em
``/api/resource_management/v4/resources/{id}/attributes``, em paralelo.

O tipo do computador (servidor físico, VM, notebook, desktop) é deduzido:
modelo de placa virtual → VM; SO de servidor → servidor; CPU de linha móvel →
notebook; o resto → desktop.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from itgov.utils.cache_swr import CacheSWR

log = structlog.get_logger(__name__)

TIPOS_COMPUTADOR: dict[str, str] = {
    "servidor": "Servidor físico",
    "vm": "Máquina virtual",
    "notebook": "Notebook",
    "desktop": "Desktop",
}

_PARALELO = 8
_VIRTUAL = re.compile(r"virtual machine|vmware|virtualbox|kvm|qemu|hvm domu|xen|440bx desktop reference|hyper-v", re.I)
# Sufixos de CPU de notebook: i5-10210U, i7-12700H, Ryzen 5 5500U, Core Ultra 7 155H, Celeron N4020
_CPU_MOVEL = re.compile(
    r"\b(?:i[3579]|ryzen \d(?: pro)?)[- ]\d{4,5}(?:u|h|hq|hs|hx|hk|p|y|g\d)\b|ultra \d \d{3}[huv]\b"
    r"|\b(?:i3-)?n\d{3,4}\b|\b[j]\d{4}\b|\bm\d \d{4}",
    re.I,
)


class Computador(BaseModel):
    """Um computador com agente Acronis."""

    nome: str
    tipo: str
    usuario: str = ""
    fabricante: str = ""
    modelo: str = ""
    so: str = ""
    cpu: str = ""
    nucleos: int = 0
    ram_gb: float = 0.0
    disco_gb: float = 0.0
    disco_livre_gb: float = 0.0
    disco_tipo: str = ""
    serie: str = ""
    ip: str = ""
    online: bool = False
    inventario_em: datetime | None = None

    @property
    def disco_uso_pct(self) -> float:
        """Percentual de disco ocupado (0 sem dado)."""
        return round(100 * (1 - self.disco_livre_gb / self.disco_gb), 1) if self.disco_gb else 0.0


class InventarioComputadores(BaseModel):
    """Resultado da coleta."""

    atualizado_em: datetime
    computadores: list[Computador]
    sem_inventario: int = 0

    @property
    def por_tipo(self) -> dict[str, int]:
        """Contagem por tipo, na ordem de ``TIPOS_COMPUTADOR``."""
        contagem = dict.fromkeys(TIPOS_COMPUTADOR, 0)
        for c in self.computadores:
            contagem[c.tipo] = contagem.get(c.tipo, 0) + 1
        return {t: n for t, n in contagem.items() if n}


def configurado() -> bool:
    """Credenciais do Acronis presentes no ambiente?"""
    return bool(os.getenv("ACRONIS_BASE_URL") and os.getenv("ACRONIS_CLIENT_ID") and os.getenv("ACRONIS_CLIENT_SECRET"))


def _gb(valor: Any) -> float:
    try:
        return round(int(str(valor).strip('"')) / 1024**3, 1)
    except (TypeError, ValueError):
        return 0.0


def tipo_computador(modelo: str, fabricante: str, so: str, produto_so: str, cpu: str, wifi: bool = False) -> str:
    """Deduz o tipo do computador.

    Args:
        modelo: Modelo da placa-mãe.
        fabricante: Fabricante da placa-mãe.
        so: Nome do sistema operacional.
        produto_so: ``os_product_type`` do Windows (1 = estação; 2/3 = servidor).
        cpu: Nome do processador.
        wifi: Tem placa Wi-Fi (decide quando a CPU não veio no inventário).

    Returns:
        Chave de ``TIPOS_COMPUTADOR``.
    """
    if _VIRTUAL.search(f"{fabricante} {modelo}"):
        return "vm"
    if "server" in so.lower() or produto_so in ("2", "3"):
        return "servidor"
    if _CPU_MOVEL.search(cpu) or (not cpu and wifi):
        return "notebook"
    return "desktop"


def montar_computador(recurso: dict[str, Any], atributos: list[dict[str, Any]]) -> Computador | None:
    """Junta os namespaces do Acronis num ``Computador``.

    Args:
        recurso: Item de ``resource_management/v4/resources`` (tipo máquina).
        atributos: ``items`` de ``.../resources/{id}/attributes``.

    Returns:
        O computador, ou None se não houver inventário de hardware (``hwi``).
    """
    ns = {it.get("name"): it for it in atributos}
    hwi = ns.get("hwi")
    if not hwi:
        return None
    kv = {n: {k["key"]: k["value"] for k in (it.get("kvs") or [])} for n, it in ns.items()}
    det = hwi.get("details") or {}
    placa = (det.get("motherBoards") or [{}])[0]
    discos = det.get("disks") or []
    agente, padrao, hw = kv.get("agent", {}), kv.get("default", {}), kv.get("hwi", {})
    so = str(agente.get("os_name") or padrao.get("operating_system") or "")
    cpus = [str(x.get("name") or "") for x in det.get("cpus") or []]
    cpu = str(cpus[0] if cpus else hw.get("cpu") or "")
    if cpu.startswith("["):  # várias CPUs vêm como texto de lista
        cpu = cpu.strip("[]").split("', ")[0].strip("'\"")
    wifi = any("802.11" in str(n.get("type") or "") for n in det.get("networkAdapters") or [])
    ram = _gb(agente.get("memory_size")) or round(
        sum(int(r.get("capacity") or 0) for r in det.get("rams") or []) / 1024**3, 1
    )
    usuario = str(kv.get("atp", {}).get("last_logged_in_user") or "")
    scan = hw.get("scan_time")
    nome = str(recurso.get("user_defined_name") or recurso.get("name") or "").split(".")[0]
    return Computador(
        nome=nome,
        tipo=tipo_computador(
            str(placa.get("model") or ""), str(placa.get("manufacturer") or ""), so,
            str(agente.get("os_product_type") or padrao.get("os_product_type") or ""), cpu, wifi,
        ),
        usuario=usuario,
        fabricante=str(placa.get("manufacturer") or "").strip(),
        modelo=str(placa.get("model") or "").strip(),
        so=so,
        cpu=re.sub(r"\s+", " ", re.sub(r"\((?:R|TM)\)|CPU", "", cpu)).strip(),
        nucleos=int(hw.get("cores") or 0),
        ram_gb=ram,
        disco_gb=round(sum(int(d.get("size") or 0) for d in discos) / 1024**3, 1) or _gb(hw.get("disk_size")),
        disco_livre_gb=round(sum(int(d.get("availableSpace") or 0) for d in discos) / 1024**3, 1),
        disco_tipo="/".join(sorted({str(d.get("mediaType")) for d in discos if d.get("mediaType")})),
        serie=str(hw.get("serial_number") or placa.get("serialNumber") or ""),
        ip=str(padrao.get("ip") or "").split(",")[0],
        online=str(agente.get("online", "")).lower() == "true",
        inventario_em=datetime.fromtimestamp(int(scan) / 1e9, UTC) if str(scan or "").isdigit() else None,
    )  # fmt: skip


async def _buscar() -> InventarioComputadores:
    base = os.getenv("ACRONIS_BASE_URL", "").rstrip("/")
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(
            f"{base}/api/2/idp/token",
            auth=(os.getenv("ACRONIS_CLIENT_ID", ""), os.getenv("ACRONIS_CLIENT_SECRET", "")),
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        c.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        recursos: list[dict[str, Any]] = []
        params: dict[str, Any] = {"type": "resource.machine", "limit": 500}
        while True:
            pagina = await c.get(f"{base}/api/resource_management/v4/resources", params=params)
            pagina.raise_for_status()
            dados = pagina.json()
            recursos.extend(dados.get("items", []))
            depois = (dados.get("paging") or {}).get("cursors", {}).get("after")
            if not depois or not dados.get("items") or len(recursos) >= 5000:
                break
            params["after"] = depois

        limite = asyncio.Semaphore(_PARALELO)

        async def _attrs(rec: dict[str, Any]) -> Computador | None:
            async with limite:
                r = await c.get(f"{base}/api/resource_management/v4/resources/{rec['id']}/attributes")
            if r.status_code != 200:
                return None
            return montar_computador(rec, r.json().get("items", []))

        achados = await asyncio.gather(*(_attrs(r) for r in recursos))
    computadores = sorted((x for x in achados if x), key=lambda x: (x.tipo, x.nome.lower()))
    return InventarioComputadores(
        atualizado_em=datetime.now(UTC), computadores=computadores, sem_inventario=len(recursos) - len(computadores)
    )


def _carregar() -> InventarioComputadores | None:
    try:
        return asyncio.run(_buscar())
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as exc:
        log.warning("acronis_inventario.falhou", erro=type(exc).__name__, detalhe=str(exc)[:200])
        return None


# O inventário do agente é refeito uma vez por dia; 270 chamadas, ~30 s
_cache: CacheSWR[InventarioComputadores | None] = CacheSWR(
    "acronis.inventario", ttl=6 * 3600, valido=lambda v: v is not None, ttl_falha=600
)


def obter_inventario() -> InventarioComputadores | None:
    """Inventário dos computadores (cache de 6 h).

    Não espera a primeira coleta (~30 s): enquanto ela roda em segundo plano,
    devolve None e ``carregando()`` fica True.

    Returns:
        O inventário; None sem Acronis, durante a primeira coleta ou se falhou.
    """
    if not configurado():
        return None
    if not _cache.carregado:
        aquecer()
        return None
    return _cache.get(_carregar)


def carregando() -> bool:
    """A primeira coleta ainda não terminou?"""
    return configurado() and not _cache.carregado


def aquecer() -> None:
    """Carrega o cache em segundo plano na subida do worker."""
    if configurado():
        _cache.aquecer(_carregar)
