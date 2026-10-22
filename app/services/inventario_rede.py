"""Modelo, nº de série e MAC de cada IP, para a página Rede e o Mapa de Câmeras.

As fontes, da mais confiável para a menos:

1. O gravador Intelbras (leitura de ``cftv_gravadores``): modelo e série dele e,
   de cada câmera IP ligada a ele, modelo, série, MAC e fabricante. Nada disso
   exige logar na câmera.
2. A descoberta de rede (FortiGate/nmap): MAC e fabricante.
3. A IA da página Rede: modelo provável, quando os sinais do aparelho dizem
   (fica marcado como sugestão da IA).

Também separa os dispositivos em gravadores (DVR/NVR), câmeras e outros.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

GRUPOS: dict[str, str] = {"gravadores": "Gravadores (DVR/NVR)", "cameras": "Câmeras", "outros": "Outros"}

# Linhas de gravador por modelo: Intelbras MHDX/HDCVI/XVR e Hikvision DS-xxxxH* = DVR;
# Intelbras NVD/iNVD e Hikvision DS-xxxxN* = NVR
_DVR = re.compile(r"\b(?:MHDX|HDCVI|XVR|DVR)|\bDS-\d{4}H", re.I)
_NVR = re.compile(r"\b(?:I?NVD|NVR)|\bDS-\d{4}N", re.I)
# Nome ou modelo que denuncia gravador num host que não foi lido pela API
_GRAVADOR = re.compile(r"\b(?:DVR|NVR|I?NVD|MHDX|XVR)", re.I)


class InfoEquipamento(BaseModel):
    """O que se sabe de um equipamento; campos vazios quando a fonte não informou.

    ``fonte``: ``gravador`` (lido pela API do gravador), ``rede`` (descoberta)
    ou ``ia`` (modelo sugerido pela IA). ``equipamento``: ``DVR``, ``NVR`` ou
    ``Gravador`` para gravadores; vazio para o resto.
    """

    modelo: str = ""
    serie: str = ""
    mac: str = ""
    fabricante: str = ""
    fonte: str = ""
    equipamento: str = ""
    gravador_ip: str = ""
    canal: int | None = None


def tipo_gravador(modelo: str, analogicas: int = 0) -> str:
    """DVR, NVR ou Gravador (quando nem o modelo nem as câmeras dizem).

    Args:
        modelo: Modelo informado (ex.: ``MHDX 1116``, ``iNVD 7032``).
        analogicas: Câmeras analógicas ligadas (só DVR tem).
    """
    if _NVR.search(modelo or ""):
        return "NVR"
    if _DVR.search(modelo or "") or analogicas:
        return "DVR"
    return "Gravador"


def inventario(leituras: dict[str, dict[str, Any]], hosts: list[dict[str, Any]]) -> dict[str, InfoEquipamento]:
    """IP → o que se sabe do equipamento, juntando gravadores, descoberta e IA.

    Args:
        leituras: ``cftv_gravadores.leituras()`` (IP do gravador → leitura).
        hosts: Hosts da página Rede (``mac``, ``vendor``, ``ia_modelo``).

    Returns:
        Um item por IP com alguma informação.
    """
    info: dict[str, InfoEquipamento] = {}
    for ip, dados in leituras.items():
        leitura = dados.get("leitura")
        if leitura is None:
            continue
        analogicas = sum(1 for c in leitura.cameras if c.tipo == "analogica")
        info[ip] = InfoEquipamento(
            modelo=leitura.modelo,
            serie=leitura.serie,
            fonte="gravador",
            equipamento=tipo_gravador(leitura.modelo, analogicas),
        )
        for cam in leitura.cameras:
            if cam.tipo != "ip" or not cam.ip or cam.ip in info:
                continue
            info[cam.ip] = InfoEquipamento(
                modelo=cam.modelo,
                serie=cam.serie,
                mac=cam.mac,
                fabricante=cam.fabricante,
                fonte="gravador",
                gravador_ip=ip,
                canal=cam.canal,
            )
    for h in hosts:
        ip = h.get("ip")
        if not ip:
            continue
        item = info.get(ip) or InfoEquipamento()
        item.mac = item.mac or str(h.get("mac") or "").lower()
        item.fabricante = item.fabricante or str(h.get("vendor") or "")
        if not item.modelo and (ia := str(h.get("ia_modelo") or "")):
            item.modelo, item.fonte = ia, item.fonte or "ia"
        item.fonte = item.fonte or "rede"
        if item.modelo or item.serie or item.mac or item.fabricante:
            info[ip] = item
    return info


def grupo(host: dict[str, Any], info: InfoEquipamento | None, gravadores: set[str], cameras: set[str]) -> str:
    """Em que aba o host fica: ``gravadores``, ``cameras`` ou ``outros``.

    Args:
        host: Host da página Rede.
        info: Inventário do IP (pode faltar).
        gravadores: IPs de gravador (lidos pela API ou da conferência do CFTV).
        cameras: IPs de câmera (informados pelos gravadores ou pela conferência).
    """
    ip = host.get("ip", "")
    if ip in gravadores or (info is not None and info.equipamento):
        return "gravadores"
    if ip in cameras:
        return "cameras"
    if host.get("tipo_sugerido") == "camera":
        sinais = " ".join(str(host.get(c) or "") for c in ("zabbix_host", "ia_nome", "hostname"))
        if _GRAVADOR.search(sinais) or (info is not None and _GRAVADOR.search(info.modelo)):
            return "gravadores"
        return "cameras"
    return "outros"


def rotulo_equipamento(host: dict[str, Any], info: InfoEquipamento | None) -> str:
    """DVR/NVR/Gravador para quem está na aba de gravadores; vazio para o resto."""
    if info is not None and info.equipamento:
        return info.equipamento
    sinais = " ".join(str(host.get(c) or "") for c in ("zabbix_host", "ia_nome", "hostname"))
    return tipo_gravador(f"{info.modelo if info else ''} {sinais}")


def casa_busca(host: dict[str, Any], info: InfoEquipamento | None, termo: str, rotulo_tipo: str) -> bool:
    """O host aparece na busca? Procura em IP, tipo, nome, modelo, série e MAC.

    Args:
        host: Host da página Rede.
        info: Inventário do IP.
        termo: Texto digitado (sem diferenciar maiúsculas).
        rotulo_tipo: Rótulo do tipo sugerido (ex.: "Câmera / DVR").
    """
    termo = termo.strip().lower()
    if not termo:
        return True
    campos = [
        host.get("ip", ""),
        rotulo_tipo,
        host.get("tipo_sugerido", ""),
        host.get("ia_nome", ""),
        host.get("hostname", ""),
    ]
    if info is not None:
        campos += [info.modelo, info.serie, info.mac, info.equipamento]
    return any(termo in str(c or "").lower() for c in campos)
