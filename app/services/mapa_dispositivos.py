"""Dispositivos de rede de uma unidade para o Mapa de Câmeras.

O MapaCameras (botão "Dispositivos da rede") lê esta lista para cadastrar,
sem posição, o que ainda não está no mapa. Entra tudo o que a descoberta
de rede achou nas faixas da unidade, menos celular/tablet. Câmera e
gravador saem da conferência do CFTV (``CanalCftv``): a câmera leva o
gravador e o canal; o gravador vira "Dispositivo de Infra". Modelo, nº de
série, MAC e fabricante vêm do inventário (``app.services.inventario_rede``):
o que o próprio gravador Intelbras informou, completado pela descoberta.

Câmera analógica não tem IP: entra pelo par gravador + canal (``chave``
``<ip do gravador>#<canal>``), com o nome do canal no DVR quando alguém o
renomeou.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

from itgov.api.v1.rede_descoberta import eh_celular
from itgov.api.v1.rede_monitoring import unidade_do_ip
from itgov.services.intelbras_api import titulo_padrao

if TYPE_CHECKING:
    from app.services.inventario_rede import InfoEquipamento


# Canal que o próprio DVR nomeou como vazio: "S/ CÂMERA", "SEM CAMERA", "s/cam"
_SEM_CAMERA = re.compile(r"^\s*(?:s/?|sem)\s*c[aâ]m", re.I)


class CanalConferido(NamedTuple):
    """Canal de gravador na conferência no local; ``ip_camera`` só quando há câmera IP."""

    ip_gravador: str
    canal: int
    ip_camera: str


class CanalAnalogico(NamedTuple):
    """Câmera analógica ligada a um canal de DVR; ``nome`` é o título do canal (pode ser vazio)."""

    ip_gravador: str
    canal: int
    nome: str = ""


def analogicas_dos_gravadores(
    leituras: dict[str, dict[str, Any]], cadastro: dict[str, dict[int, dict[str, Any]]]
) -> list[CanalAnalogico]:
    """Câmeras analógicas em uso nos DVRs, pelo mesmo critério da página do CFTV.

    O DVR lista todo canal analógico como ``Local``, com câmera ou não. Conta
    como câmera: o canal conferido no local como câmera analógica; sem
    conferência, o canal ``Local`` com vídeo ou com nome dado no gravador.
    Canal conferido como vazio, ou nomeado no DVR como sem câmera ("S/ CÂMERA")
    sem conferência dizendo o contrário, não entra.

    Args:
        leituras: ``cftv_gravadores.leituras()``.
        cadastro: ``cftv_gravadores.cadastro_canais()`` (IP → canal → conferência).

    Returns:
        Um item por canal, em ordem de gravador e canal.
    """
    achadas: dict[tuple[str, int], CanalAnalogico] = {}
    for ip, dados in leituras.items():
        leitura = dados.get("leitura")
        if leitura is None:
            continue
        conferidos = cadastro.get(ip, {})
        for cam in leitura.cameras:
            situacao = conferidos.get(cam.canal, {}).get("situacao")
            if cam.tipo != "analogica" or situacao in ("vazio", "outro"):
                continue
            titulo = leitura.titulos.get(cam.canal, "")
            vazio = titulo_padrao(titulo) or bool(_SEM_CAMERA.match(titulo))
            if situacao != "ativo" and (_SEM_CAMERA.match(titulo) or (cam.canal in leitura.sem_video and vazio)):
                continue
            nome = "" if vazio else titulo.strip()
            achadas[(ip, cam.canal)] = CanalAnalogico(ip, cam.canal, nome)
    for ip, canais in cadastro.items():
        for canal, conf in canais.items():
            if conf.get("analogica") and conf.get("situacao") == "ativo" and (ip, canal) not in achadas:
                achadas[(ip, canal)] = CanalAnalogico(ip, canal, str(conf.get("equipamento") or "").strip())
    return [achadas[k] for k in sorted(achadas, key=lambda k: (_ip_key(k[0]), k[1]))]


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
    analogicas: list[CanalAnalogico] | None = None,
) -> dict[str, Any]:
    """Dispositivos da descoberta que ficam nas faixas da unidade, sem celulares.

    Args:
        hosts: Hosts da descoberta já com as classificações manuais aplicadas.
        faixas: Pares ``(unidade_id, cidr)`` das unidades ativas.
        ids_unidade: Ids aceitos (a unidade e as filhas).
        canais: Canais da conferência do CFTV (``ip_camera`` vazio = sem câmera IP).
        inventario: IP → modelo, série, MAC e fabricante (``inventario_rede.inventario``).
        analogicas: Câmeras analógicas dos DVRs (sem IP).

    Returns:
        ``dispositivos`` (um por IP, e um por canal analógico, ordenados por
        tipo, IP e canal), ``celulares`` (quantos ficaram de fora por serem
        celular/tablet) e ``analogicas`` (quantas câmeras analógicas entraram).
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
            "analogica": False,
            "chave": "",
        }
        # gravador com o mesmo IP de uma câmera (erro de conferência) continua gravador
        if ip not in gravadores and (conf := camera_em.get(ip)):
            item["gravador_ip"] = conf.ip_gravador
            item["gravador_nome"] = _nome(por_ip.get(conf.ip_gravador, {}))
            item["canal"] = conf.canal
            item["chave"] = f"{conf.ip_gravador}#{conf.canal}"
            item["nome"] = item["nome"] or f"{item['gravador_nome'] or conf.ip_gravador} - canal {conf.canal}"
        item["nome"] = item["nome"] or ip
        saida.append(item)
    vistos: set[tuple[str, int]] = set()
    for a in analogicas or []:
        if (a.ip_gravador, a.canal) in vistos or unidade_do_ip(a.ip_gravador, faixas) not in ids_unidade:
            continue
        vistos.add((a.ip_gravador, a.canal))
        gravador = por_ip.get(a.ip_gravador, {})
        nome_gravador = _nome(gravador) or a.ip_gravador
        saida.append(
            {
                "ip": "",
                "mac": "",
                "nome": a.nome.strip() or f"{nome_gravador} - canal {a.canal} (analógica)",
                "tipo": "camera",
                "marca": "",
                "modelo": "",
                "serie": "",
                "equipamento": "",
                "zabbix_host": "",
                "online": bool(gravador.get("online")),
                "ultimo_visto": "",
                "gravador_ip": a.ip_gravador,
                "gravador_nome": nome_gravador,
                "canal": a.canal,
                "analogica": True,
                "chave": f"{a.ip_gravador}#{a.canal}",
            }
        )
    saida.sort(key=lambda d: (d["tipo"], _ip_key(d["ip"] or d["gravador_ip"]), d["canal"] or 0))
    return {"dispositivos": saida, "celulares": celulares, "analogicas": len(vistos)}
