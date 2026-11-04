"""Corrige no Zabbix o IP das câmeras que mudaram de endereço (agente de hora em hora).

O Zabbix pinga cada câmera no IP cadastrado no host. Quando a câmera troca de
IP (DHCP, troca de equipamento), o ping cai, o problema abre e o WhatsApp avisa
"câmera fora" — mas o gravador continua recebendo vídeo dela no IP novo.

A cada ``INTERVALO`` o agente:

1. lê todos os gravadores (API Intelbras), sem depender de alguém abrir a tela;
2. para cada câmera **fora no Zabbix** com canal, pega o IP que o gravador usa
   naquele canal — da leitura da API (se for de menos de ``LEITURA_FRESCA`` e o
   canal tiver vídeo) ou do SNMP do gravador (canal ``Connected``);
3. se o IP for outro, válido, da rede interna e de nenhum outro host CFTV,
   troca no Zabbix as interfaces do host que estavam no IP antigo.

Na mesma rodada, para ter um host só por câmera e nenhuma câmera sem host:

4. câmera fora cujo IP novo já é de outro host CFTV **online** é cópia (a mesma
   câmera gravada por dois gravadores): o host da cópia é desativado;
5. vários hosts de câmera ativos com o mesmo IP: fica um (o que a leitura do
   gravador confirma no canal, depois o online, depois o mais antigo) e os
   outros são desativados — senão a câmera que cai avisa duas, três vezes;
6. câmera IP com vídeo num gravador e sem host nenhum no Zabbix (nem
   desativado) ganha host com ping, como as outras (``origem=dashboard``).

Desativar é reversível (``status=1``). Cada troca, recusa, desativação ou
criação fica em ``CftvIpCorrecao`` e aparece no /gov/cftv.
No máximo ``MAX_POR_RODADA`` trocas por rodada: uma leitura com defeito não
consegue reescrever o cadastro inteiro.
"""

from __future__ import annotations

import contextlib
import fcntl
import ipaddress
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx
import requests
import structlog
from sqlalchemy.exc import SQLAlchemyError

from itgov.services.intelbras_api import SEM_ENDERECO

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

INTERVALO = timedelta(hours=1)
LEITURA_FRESCA = timedelta(hours=2)
MAX_POR_RODADA = 20
_PRIMEIRA_RODADA_S = 120
_TRAVA = os.path.join(tempfile.gettempdir(), "itgov-cftv-ip-sync.lock")

# Situação de cada correção registrada
CORRIGIDO = "corrigido"
CONFLITO = "conflito"  # o IP novo já é de outro host CFTV no Zabbix
FALHOU = "falhou"  # o Zabbix recusou ou o host não tinha interface no IP antigo
DESATIVADO = "desativado"  # cópia de câmera já monitorada em outro host
CRIADO = "criado"  # câmera com vídeo no gravador e sem host no Zabbix

MAX_CRIAR_POR_RODADA = 20
GRUPO_CAMERAS = "CFTV/Cameras"
TEMPLATE_PING = "ICMP Ping"
_FABRICANTES = {"itb": "Intelbras", "intelbras": "Intelbras"}


class Correcao(NamedTuple):
    """Câmera fora no Zabbix com IP diferente do que o gravador usa no canal."""

    hostid: str
    host: str
    nome: str
    gravador: str
    canal: int
    ip_antigo: str
    ip_novo: str
    fonte: str  # "api" ou "snmp"


def _agora() -> datetime:
    return datetime.now(UTC)


def ip_valido(ip: str) -> bool:
    """IPv4 da rede interna, que não seja endereço vazio/padrão de fábrica."""
    if ip in SEM_ENDERECO:
        return False
    try:
        end = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return end.version == 4 and end.is_private and not end.is_loopback and not end.is_link_local


def _ip_do_canal(card: dict[str, Any], acesso: dict[str, Any] | None, canal: int, agora: datetime) -> tuple[str, str]:
    """IP que o gravador usa no canal, se ele está recebendo vídeo: ``(ip, fonte)`` ou ``("", "")``."""
    from app.services.cftv_gravadores import _snmp_do_card

    acesso = acesso or {}
    leitura = acesso.get("leitura")
    lido = acesso.get("lido_ok_em")
    if leitura is not None and lido is not None and agora - lido <= LEITURA_FRESCA:
        cam = next((c for c in leitura.cameras if c.canal == canal), None)
        if cam is not None and cam.tipo == "ip" and cam.ip and canal not in leitura.sem_video:
            return cam.ip, "api"
    snmp, _ = _snmp_do_card(card)
    s = snmp.get(canal, {})
    if s.get("status") == "Connected" and s.get("ip", "") not in SEM_ENDERECO:
        return s["ip"], "snmp"
    return "", ""


def candidatos(cards: list[dict[str, Any]], acessos: dict[str, dict[str, Any]], agora: datetime) -> list[Correcao]:
    """Câmeras fora no Zabbix cujo gravador recebe vídeo delas em outro IP.

    Args:
        cards: Cards de ``montar_visao`` (com ``dispositivos`` e ``gravador_host``).
        acessos: ``cftv_gravadores.leituras()``.
        agora: Horário da rodada (UTC).

    Returns:
        As correções a fazer, sem repetir host.
    """
    achadas: dict[str, Correcao] = {}
    for card in cards:
        ip_gravador = (card.get("gravador_host") or {}).get("ip", "")
        if not ip_gravador:
            continue
        for d in card.get("dispositivos") or []:
            canal = str(d.get("canal", ""))
            if d.get("is_gravador") or d.get("status") != "down" or not canal.isdigit() or not d.get("hostid"):
                continue
            novo, fonte = _ip_do_canal(card, acessos.get(ip_gravador), int(canal), agora)
            if not novo or novo == d.get("ip") or novo == ip_gravador or not ip_valido(novo):
                continue
            achadas.setdefault(
                d["hostid"],
                Correcao(
                    d["hostid"],
                    d.get("host", ""),
                    d.get("name", ""),
                    card.get("titulo", ""),
                    int(canal),
                    d.get("ip", ""),
                    novo,
                    fonte,
                ),
            )
    return list(achadas.values())


def _trocar_no_zabbix(c: Correcao) -> int:
    """Troca no host as interfaces que estavam no IP antigo. Devolve quantas trocou.

    Raises:
        RuntimeError: O Zabbix devolveu erro.
        requests.RequestException: Falha de rede.
    """
    from itgov.api.v1.cftv_monitoring import _zbx

    interfaces = _zbx("hostinterface.get", {"hostids": [c.hostid], "output": ["interfaceid", "ip"]})
    trocadas = 0
    for i in interfaces:
        if i.get("ip") == c.ip_antigo:
            _zbx("hostinterface.update", {"interfaceid": i["interfaceid"], "ip": c.ip_novo})
            trocadas += 1
    return trocadas


def aplicar(
    correcoes: list[Correcao], ips_cftv: set[str], online_por_ip: dict[str, str] | None = None
) -> dict[str, int]:
    """Faz as trocas no Zabbix e registra cada uma. Requer app context.

    Args:
        correcoes: De ``candidatos``.
        ips_cftv: IPs de todos os hosts CFTV no Zabbix (para não duplicar).
        online_por_ip: IP → nome do host de câmera online nele. IP novo já
            monitorado online em outro host = a correção é uma cópia: desativa.

    Returns:
        Situação → quantas.
    """
    from app.extensions import db
    from app.models.cftv_ip import CftvIpCorrecao

    contagem: dict[str, int] = {}
    for c in correcoes[:MAX_POR_RODADA]:
        dono = (online_por_ip or {}).get(c.ip_novo)
        if c.ip_novo in ips_cftv and dono:
            situacao, detalhe = _desativar(c.hostid, c.host, f"cópia: a câmera já é monitorada em {dono} (online)")
        elif c.ip_novo in ips_cftv:
            situacao, detalhe = CONFLITO, f"{c.ip_novo} já é de outro host CFTV no Zabbix"
        else:
            try:
                trocadas = _trocar_no_zabbix(c)
            except (RuntimeError, requests.RequestException) as exc:
                log.warning("cftv_ip_sync.zabbix_falhou", host=c.host, erro=f"{type(exc).__name__}: {exc}"[:200])
                situacao, detalhe = FALHOU, "o Zabbix não aceitou a troca (detalhe no log)"
            else:
                if trocadas:
                    situacao, detalhe = CORRIGIDO, f"{trocadas} interface(s) trocada(s)"
                    ips_cftv.add(c.ip_novo)
                else:
                    situacao, detalhe = FALHOU, f"o host não tem interface no IP {c.ip_antigo}"
        # Conflito repetido de hora em hora não precisa de uma linha nova por rodada
        if situacao == CONFLITO and _conflito_ja_registrado(c):
            continue
        db.session.add(
            CftvIpCorrecao(
                hostid=c.hostid,
                host=c.host[:128],
                nome=c.nome[:200],
                gravador=c.gravador[:120],
                canal=c.canal,
                ip_antigo=c.ip_antigo,
                ip_novo=c.ip_novo,
                fonte=c.fonte,
                situacao=situacao,
                detalhe=detalhe[:255],
            )
        )
        db.session.commit()
        contagem[situacao] = contagem.get(situacao, 0) + 1
        log.info(
            "cftv_ip_sync.correcao",
            host=c.host,
            canal=c.canal,
            gravador=c.gravador,
            de=c.ip_antigo,
            para=c.ip_novo,
            fonte=c.fonte,
            situacao=situacao,
        )
    if len(correcoes) > MAX_POR_RODADA:
        log.warning("cftv_ip_sync.limite", pendentes=len(correcoes) - MAX_POR_RODADA)
    return contagem


def _desativar(hostid: str, host: str, motivo: str) -> tuple[str, str]:
    """Desativa o host no Zabbix (``status=1``, reversível): ``(situação, detalhe)``."""
    from itgov.api.v1.cftv_monitoring import _zbx

    try:
        _zbx("host.update", {"hostid": hostid, "status": 1})
    except (RuntimeError, requests.RequestException) as exc:
        log.warning("cftv_ip_sync.desativar_falhou", host=host, erro=f"{type(exc).__name__}: {exc}"[:200])
        return FALHOU, "o Zabbix não aceitou desativar o host (detalhe no log)"
    return DESATIVADO, f"host desativado — {motivo}"


def _registrar(
    hostid: str,
    host: str,
    nome: str,
    gravador: str,
    canal: int,
    ip_antigo: str,
    ip_novo: str,
    fonte: str,
    situacao: str,
    detalhe: str,
) -> None:
    from app.extensions import db
    from app.models.cftv_ip import CftvIpCorrecao

    db.session.add(
        CftvIpCorrecao(
            hostid=hostid,
            host=host[:128],
            nome=nome[:200],
            gravador=gravador[:120],
            canal=canal,
            ip_antigo=ip_antigo,
            ip_novo=ip_novo,
            fonte=fonte,
            situacao=situacao,
            detalhe=detalhe[:255],
        )
    )
    db.session.commit()
    log.info("cftv_ip_sync.registro", host=host, gravador=gravador, canal=canal, ip=ip_novo, situacao=situacao)


def _canal_int(valor: object) -> int:
    texto = str(valor or "")
    return int(texto) if texto.isdigit() else 0


def _ips_dos_gravadores(
    cards: list[dict[str, Any]], acessos: dict[str, dict[str, Any]], agora: datetime
) -> dict[str, dict[int, Any]]:
    """Gravador (tag ``dvr``) → canal → câmera IP com vídeo, das leituras frescas da API."""
    resultado: dict[str, dict[int, Any]] = {}
    for card in cards:
        ip_gravador = (card.get("gravador_host") or {}).get("ip", "")
        acesso = acessos.get(ip_gravador) or {}
        leitura, lido = acesso.get("leitura"), acesso.get("lido_ok_em")
        if not ip_gravador or leitura is None or lido is None or agora - lido > LEITURA_FRESCA:
            continue
        resultado[card.get("gravador", "")] = {
            cam.canal: cam
            for cam in leitura.cameras
            if cam.tipo == "ip" and ip_valido(cam.ip) and cam.ip != ip_gravador and cam.canal not in leitura.sem_video
        }
    return resultado


def duplicados(
    devices: list[dict[str, Any]], confirmados: dict[str, dict[int, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Hosts de câmera ativos repetidos no mesmo IP: ``[(host a desativar, host que fica), ...]``.

    Fica o que a leitura do gravador confirma (mesmo IP no canal do host), depois
    o online, depois o de menor hostid (o mais antigo).

    Args:
        devices: ``get_cached_cftv_summary()["devices"]`` (só hosts ativos).
        confirmados: De ``_ips_dos_gravadores``.
    """
    por_ip: dict[str, list[dict[str, Any]]] = {}
    for d in devices:
        if d.get("subcat") == "camera" and not d.get("is_gravador") and ip_valido(d.get("ip") or ""):
            por_ip.setdefault(d["ip"], []).append(d)
    pares = []
    for ip, hosts in por_ip.items():
        if len(hosts) < 2:
            continue

        def ordem(d: dict[str, Any], ip: str = ip) -> tuple[int, int, int]:
            cam = confirmados.get(d.get("gravador", ""), {}).get(_canal_int(d.get("canal")))
            return (0 if cam is not None and cam.ip == ip else 1, 0 if d.get("status") == "up" else 1, int(d["hostid"]))

        fica, *sobra = sorted(hosts, key=ordem)
        pares.extend((d, fica) for d in sobra)
    return pares


def faltantes(
    cards: list[dict[str, Any]],
    confirmados: dict[str, dict[int, Any]],
    ips_zabbix: set[str],
    outros: set[tuple[str, int]],
) -> list[tuple[dict[str, Any], Any, list[str]]]:
    """Câmeras IP com vídeo no gravador e sem host no Zabbix: ``[(card, câmera, outros gravadores), ...]``.

    A mesma câmera em mais de um gravador ganha um host só, no primeiro; os
    outros ficam anotados.

    Args:
        cards: Cards de ``montar_visao``.
        confirmados: De ``_ips_dos_gravadores``.
        ips_zabbix: IPs de todas as interfaces do Zabbix, hosts desativados inclusive.
        outros: ``(IP do gravador, canal)`` marcados na conferência como outro equipamento.
    """
    achadas: dict[str, tuple[dict[str, Any], Any, list[str]]] = {}
    for card in cards:
        ip_gravador = (card.get("gravador_host") or {}).get("ip", "")
        for canal, cam in sorted(confirmados.get(card.get("gravador", ""), {}).items()):
            if cam.ip in ips_zabbix or (ip_gravador, canal) in outros:
                continue
            if cam.ip in achadas:
                achadas[cam.ip][2].append(f"{card.get('titulo', '')} ch{canal}")
            else:
                achadas[cam.ip] = (card, cam, [])
    return list(achadas.values())


def _criar_host_camera(card: dict[str, Any], cam: Any, hosts_existentes: set[str]) -> str:
    """Cria o host de ping da câmera, como os outros de ``CFTV/Cameras``. Devolve o hostid.

    Raises:
        RuntimeError: Grupo/template não existe ou o Zabbix recusou.
        requests.RequestException: Falha de rede.
    """
    from itgov.api.v1.cftv_monitoring import _zbx
    from itgov.services.cftv_snmp import nome_tecnico

    grupos = _zbx("hostgroup.get", {"output": ["groupid"], "filter": {"name": [GRUPO_CAMERAS]}})
    templates = _zbx("template.get", {"output": ["templateid"], "filter": {"host": [TEMPLATE_PING]}})
    if not grupos or not templates:
        raise RuntimeError(f"grupo {GRUPO_CAMERAS} ou template {TEMPLATE_PING} não existe no Zabbix")
    gravador = card.get("gravador", "")
    base = "cam-" + nome_tecnico(gravador).lower().replace(" ", "-").replace("_", "-") + f"-ch{cam.canal:02d}"
    tecnico = base if base not in hosts_existentes else f"{base}-{cam.ip.replace('.', '-')}"
    tags = [
        {"tag": "category", "value": "cftv"},
        {"tag": "subcategory", "value": "camera"},
        {"tag": "dvr", "value": gravador},
        {"tag": "canal", "value": str(cam.canal)},
        {"tag": "andar", "value": gravador},
        {"tag": "origem", "value": "dashboard"},
    ]
    fabricante = _FABRICANTES.get((cam.fabricante or "").lower())
    if fabricante:
        tags.append({"tag": "vendor", "value": fabricante})
    if cam.modelo:
        tags += [{"tag": "model", "value": cam.modelo}, {"tag": "model_source", "value": "gravador"}]
    criado = _zbx(
        "host.create",
        {
            "host": tecnico,
            "name": f"Câmera {gravador} - canal {cam.canal}",
            "groups": [{"groupid": grupos[0]["groupid"]}],
            "templates": [{"templateid": templates[0]["templateid"]}],
            "tags": tags,
            "interfaces": [{"type": 1, "main": 1, "useip": 1, "ip": cam.ip, "dns": "", "port": "10050"}],
        },
    )
    hosts_existentes.add(tecnico)
    return str(criado["hostids"][0])


def arrumar_hosts(
    cards: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    acessos: dict[str, dict[str, Any]],
    agora: datetime,
    ignorar: set[str] | None = None,
) -> dict[str, int]:
    """Passos 5 e 6: desativa hosts repetidos no mesmo IP e cria os que faltam. Requer app context.

    Args:
        cards: Cards de ``montar_visao``.
        devices: ``get_cached_cftv_summary()["devices"]``.
        acessos: ``cftv_gravadores.leituras()``.
        agora: Horário da rodada (UTC).
        ignorar: IPs novos das correções desta rodada (o host existe, só está no IP antigo).

    Returns:
        Situação → quantos.
    """
    from app.models.unidade import CanalCftv
    from itgov.api.v1.cftv_monitoring import _zbx

    contagem: dict[str, int] = {}
    confirmados = _ips_dos_gravadores(cards, acessos, agora)
    for d, fica in duplicados(devices, confirmados):
        situacao, detalhe = _desativar(
            d["hostid"], d.get("host", ""), f"IP repetido: a câmera fica em {fica.get('name', '')}"
        )
        _registrar(
            d["hostid"],
            d.get("host", ""),
            d.get("name", ""),
            d.get("gravador", ""),
            _canal_int(d.get("canal")),
            d["ip"],
            d["ip"],
            "dup",
            situacao,
            detalhe,
        )
        contagem[situacao] = contagem.get(situacao, 0) + 1
    try:
        interfaces = _zbx("hostinterface.get", {"output": ["ip"]})
        hosts_existentes = {h["host"] for h in _zbx("host.get", {"output": ["host"]})}
    except (RuntimeError, requests.RequestException) as exc:
        log.warning("cftv_ip_sync.inventario_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
        return contagem
    ips_zabbix = {i.get("ip", "") for i in interfaces} | (ignorar or set())
    outros = {(c.ip_gravador, c.canal) for c in CanalCftv.query.filter_by(situacao="outro")}
    novos = faltantes(cards, confirmados, ips_zabbix, outros)
    for card, cam, tambem in novos[:MAX_CRIAR_POR_RODADA]:
        gravador = card.get("gravador", "")
        try:
            hostid = _criar_host_camera(card, cam, hosts_existentes)
        except (RuntimeError, requests.RequestException) as exc:
            log.warning("cftv_ip_sync.criar_falhou", ip=cam.ip, erro=f"{type(exc).__name__}: {exc}"[:200])
            hostid, situacao, detalhe = "", FALHOU, "o Zabbix não aceitou criar o host (detalhe no log)"
        else:
            situacao = CRIADO
            detalhe = "host de ping criado" + (f"; também gravada em {', '.join(tambem)}" if tambem else "")
        _registrar(
            hostid,
            "",
            f"Câmera {gravador} - canal {cam.canal}",
            card.get("titulo", ""),
            cam.canal,
            "",
            cam.ip,
            "api",
            situacao,
            detalhe,
        )
        contagem[situacao] = contagem.get(situacao, 0) + 1
    if len(novos) > MAX_CRIAR_POR_RODADA:
        log.warning("cftv_ip_sync.limite_criar", pendentes=len(novos) - MAX_CRIAR_POR_RODADA)
    return contagem


def _conflito_ja_registrado(c: Correcao) -> bool:
    from app.models.cftv_ip import CftvIpCorrecao

    ultima = CftvIpCorrecao.query.filter_by(hostid=c.hostid).order_by(CftvIpCorrecao.quando.desc()).first()
    return ultima is not None and ultima.situacao == CONFLITO and ultima.ip_novo == c.ip_novo


def rodada(agora: datetime | None = None) -> dict[str, Any]:
    """Lê os gravadores e corrige os IPs no Zabbix. Requer app context.

    Returns:
        Quantos gravadores foram lidos, quantos candidatos e a contagem por situação.
    """
    from app.services import cftv_gravadores
    from app.views.dashboards import _visao_cftv
    from itgov.api.v1.cftv_monitoring import get_cached_cftv_summary, invalidar_cache_cftv

    agora = agora or _agora()
    visao = _visao_cftv("", disparar_leitura=False).visao
    alvos = cftv_gravadores.alvos_dos_cards(visao["cards"])
    with cftv_gravadores.ocupado():
        lidos = cftv_gravadores.atualizar(alvos)
        ips_gravadores = {ip for c in visao["cards"] if (ip := (c.get("gravador_host") or {}).get("ip"))}
        cftv_gravadores.limpar_abandonados(ips_gravadores, agora)
    correcoes = candidatos(visao["cards"], cftv_gravadores.leituras(), agora)
    devices = (get_cached_cftv_summary() or {}).get("devices", [])
    ips_cftv = {d.get("ip", "") for d in devices if d.get("ip")}
    online = {
        d["ip"]: d.get("name", "")
        for d in devices
        if d.get("ip") and not d.get("is_gravador") and d.get("status") == "up"
    }
    contagem = aplicar(correcoes, ips_cftv, online) if correcoes else {}
    acessos = cftv_gravadores.leituras()
    for situacao, n in arrumar_hosts(visao["cards"], devices, acessos, agora, {c.ip_novo for c in correcoes}).items():
        contagem[situacao] = contagem.get(situacao, 0) + n
    if contagem.get(DESATIVADO) or contagem.get(CRIADO):
        invalidar_cache_cftv()
    resultado = {"lidos": lidos, "candidatos": len(correcoes), **contagem}
    log.info("cftv_ip_sync.rodada", **resultado)
    return resultado


def ultimas(n: int = 15) -> list[dict[str, Any]]:
    """Últimas correções registradas (ver ``app.models.cftv_ip.ultimas_correcoes``)."""
    from app.models.cftv_ip import ultimas_correcoes

    return ultimas_correcoes(n)


def iniciar_agendador(app: Flask) -> bool:
    """Liga o agente neste processo se for o gunicorn e ninguém mais tiver a trava.

    Returns:
        True se este processo ficou com o agente.
    """
    if app.config.get("TESTING") or "gunicorn" not in os.path.basename(sys.argv[0]):
        return False
    if not os.getenv("ZABBIX_URL"):
        return False
    trava = open(_TRAVA, "w")  # noqa: SIM115 -- fica aberta enquanto o worker viver (segura a trava)
    try:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        trava.close()
        return False

    def _laco() -> None:
        time.sleep(_PRIMEIRA_RODADA_S)
        while True:
            try:
                with app.app_context():
                    rodada()
            except (
                SQLAlchemyError,
                httpx.HTTPError,
                requests.RequestException,
                RuntimeError,
                OSError,
                ValueError,
            ) as exc:
                log.warning("cftv_ip_sync.rodada_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
                with contextlib.suppress(SQLAlchemyError), app.app_context():
                    from app.extensions import db

                    db.session.rollback()
            time.sleep(INTERVALO.total_seconds())

    app.extensions["cftv_ip_sync_trava"] = trava
    threading.Thread(target=_laco, name="cftv-ip-sync", daemon=True).start()
    log.info("cftv_ip_sync.agendador_ligado", pid=os.getpid())
    return True
