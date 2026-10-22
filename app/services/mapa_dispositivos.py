"""Dispositivos de rede de uma unidade para o Mapa de Câmeras.

O MapaCameras (botão "Dispositivos da rede") lê esta lista para cadastrar,
sem posição, o que ainda não está no mapa. Entra tudo o que a descoberta
de rede achou nas faixas da unidade, menos celular/tablet. Câmera e
gravador saem da conferência do CFTV (``CanalCftv``): a câmera leva o
gravador e o canal; o gravador vira "Dispositivo de Infra". Modelo, nº de
série, MAC e fabricante vêm do inventário (``app.services.inventario_rede``):
o que o próprio gravador Intelbras informou, completado pela descoberta.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

from itgov.api.v1.rede_descoberta import eh_celular
from itgov.api.v1.rede_monitoring import unidade_do_ip

if TYPE_CHECKING:
    from app.services.inventario_rede import InfoEquipamento


class CanalConferido(NamedTuple):
    """Canal de gravador na conferência no local; ``ip_camera`` só quando há câmera IP."""

    ip_gravador: str
    canal: int
    ip_camera: str


def _ip_key(ip: str) -> tuple[int, ...]:
    """Ordena IPv4 pelo número (10.0.0.9 antes de 10.0.0.10); o resto vai para o fim."""
    partes = ip.split(".")
    return tuple(int(p) for p in partes) if len(partes) == 4 and all(p.isdigit() for p in partes) else (999,)


def _nome(host: dict[str, Any]) -> str:
    """Nome mais útil do host: Zabbix, depois o sugerido pela IA, depois o DHCP/DNS."""
    for campo in ("zabbix_host", "ia_nome", "hostname"):
        if valor := str(host.get(campo) or "").strip():
            return valor
    return ""


def dispositivos_da_unidade(
    hosts: list[dict[str, Any]],
    faixas: list[tuple[int, str]],
    ids_unidade: set[int],
    canais: list[CanalConferido],
    inventario: dict[str, InfoEquipamento] | None = None,
) -> dict[str, Any]:
    """Dispositivos da descoberta que ficam nas faixas da unidade, sem celulares.

    Args:
        hosts: Hosts da descoberta já com as classificações manuais aplicadas.
        faixas: Pares ``(unidade_id, cidr)`` das unidades ativas.
        ids_unidade: Ids aceitos (a unidade e as filhas).
        canais: Canais da conferência do CFTV (``ip_camera`` vazio = sem câmera IP).
        inventario: IP → modelo, série, MAC e fabricante (``inventario_rede.inventario``).

    Returns:
        ``dispositivos`` (um por IP, ordenados por tipo e IP) e ``celulares``
        (quantos ficaram de fora por serem celular/tablet).
    """
    inventario = inventario or {}
    gravadores = {c.ip_gravador for c in canais}
    camera_em = {c.ip_camera: c for c in canais if c.ip_camera}
    por_ip = {h["ip"]: h for h in hosts if h.get("ip")}
    saida: list[dict[str, Any]] = []
    celulares = 0
    # Câmera ou gravador da conferência entra mesmo se a descoberta não o viu
    for ip in sorted(set(por_ip) | set(camera_em) | gravadores):
        host = por_ip.get(ip, {})
        info = inventario.get(ip)
        if unidade_do_ip(ip, faixas) not in ids_unidade:
            continue
        if ip not in gravadores and ip not in camera_em and (eh_celular(host) or host.get("tipo_sugerido") == "movel"):
            celulares += 1
            continue
        tipo = (
            "gravador"
            if ip in gravadores
            else "camera"
            if ip in camera_em
            else str(host.get("tipo_sugerido") or "outro")
        )
        item: dict[str, Any] = {
            "ip": ip,
            "mac": (info.mac if info else "") or str(host.get("mac") or ""),
            "nome": _nome(host),
            "tipo": tipo,
            "marca": (info.fabricante if info else "") or str(host.get("vendor") or ""),
            "modelo": info.modelo if info else "",
            "serie": info.serie if info else "",
            "equipamento": info.equipamento if info else "",
            "zabbix_host": str(host.get("zabbix_host") or ""),
            "online": bool(host.get("online")),
            "ultimo_visto": (
                datetime.fromtimestamp(int(host["ultimo_visto"]), UTC).isoformat() if host.get("ultimo_visto") else ""
            ),
            "gravador_ip": "",
            "gravador_nome": "",
            "canal": None,
        }
        # gravador com o mesmo IP de uma câmera (erro de conferência) continua gravador
        if ip not in gravadores and (conf := camera_em.get(ip)):
            item["gravador_ip"] = conf.ip_gravador
            item["gravador_nome"] = _nome(por_ip.get(conf.ip_gravador, {}))
            item["canal"] = conf.canal
            item["nome"] = item["nome"] or f"{item['gravador_nome'] or conf.ip_gravador} - canal {conf.canal}"
        item["nome"] = item["nome"] or ip
        saida.append(item)
    saida.sort(key=lambda d: (d["tipo"], _ip_key(d["ip"])))
    return {"dispositivos": saida, "celulares": celulares}
