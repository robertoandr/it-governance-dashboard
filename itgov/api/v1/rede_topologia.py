"""Topologia lógica da rede, por unidade e geral.

Fontes (já lidas por outros módulos):

- FortiGates pela API (``fortigate_api``): WANs, redes internas (LAN/VLAN com
  IP e máscara) e SD-WAN — os SLAs ``Ping_<destino>`` medem os túneis entre
  unidades e para datacenters.
- Hosts da página Rede: cada um vai para a rede do FortiGate que o contém.

A topologia é lógica (operadora → FortiGate → VLAN → dispositivos). A física
(qual switch/porta) depende de SNMP/LLDP nos switches, que hoje não existe.
"""

from __future__ import annotations

import ipaddress
import math
import unicodedata
from typing import Any

# Destino do SLA "Ping_<x>" → nome do FortiGate, quando o apelido difere
APELIDOS = {"matriz": "sede", "mtz": "sede"}
# SLAs que medem internet, não um túnel
_SLAS_INTERNET = {"externo", "teste"}


def _norm(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower().strip()


def destino_do_sla(sla: str, nomes: list[str]) -> str | None:
    """Para onde aponta um SLA ``Ping_<destino>``: nome de FortiGate conhecido ou o próprio destino.

    Args:
        sla: Nome do SLA no SD-WAN.
        nomes: Nomes dos FortiGates (configurados e pendentes).

    Returns:
        O nome do FortiGate, o destino externo (ex.: "IDC") ou None quando o SLA
        não é de túnel (``Default_*``, ``Ping_Externo``).
    """
    if not sla.lower().startswith("ping_"):
        return None
    alvo = sla[5:].replace("_", " ").strip()
    chave = APELIDOS.get(_norm(alvo), _norm(alvo))
    if not alvo or chave in _SLAS_INTERNET:
        return None
    for nome in nomes:
        if _norm(nome) == chave or chave in _norm(nome) or _norm(nome) in chave:
            return nome
    return alvo


def _melhor_rede(ip: str, redes: list[tuple[str, int, ipaddress.IPv4Network | ipaddress.IPv6Network]]) -> str | None:
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return None
    melhor: tuple[int, str] | None = None
    for chave, prefixo, rede in redes:
        if endereco.version == rede.version and endereco in rede and (melhor is None or prefixo > melhor[0]):
            melhor = (prefixo, chave)
    return melhor[1] if melhor else None


def montar_topologia(
    fortigates: list[dict[str, Any]],
    pendentes: list[str],
    hosts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Topologia por unidade (FortiGate) e o mapa geral de túneis.

    Args:
        fortigates: ``fortigate_api.get_cached_fortigates`` (com ``unidade``,
            ``wans``, ``redes`` e ``sdwan``).
        pendentes: FortiGates sem token (aparecem como nó sem detalhes).
        hosts: Hosts da página Rede (``ip``, ``tipo_sugerido``, ``online``).

    Returns:
        ``unidades``: um item por FortiGate com WANs e redes (cada rede com
        total, online e contagem por tipo); ``sem_rede``: hosts fora de qualquer
        rede conhecida; ``tuneis``: arestas ``de``/``para`` com latência e status.
    """
    indice: list[tuple[str, int, ipaddress.IPv4Network | ipaddress.IPv6Network]] = []
    unidades = []
    for fw in fortigates:
        redes = []
        for r in fw.get("redes", []):
            chave = f"{fw['unidade']}|{r['iface']}"
            rede = ipaddress.ip_network(r["cidr"])
            indice.append((chave, rede.prefixlen, rede))
            redes.append({**r, "chave": chave, "total": 0, "online": 0, "por_tipo": {}})
        unidades.append(
            {
                "nome": fw["unidade"],
                "fortigate": fw.get("name", fw["unidade"]),
                "modelo": fw.get("modelo", ""),
                "host": fw.get("host", ""),
                "ok": bool(fw.get("api_up")),
                "wans": fw.get("wans", []),
                "redes": redes,
            }
        )
    por_chave = {r["chave"]: r for u in unidades for r in u["redes"]}

    sem_rede = 0
    for h in hosts:
        achada = _melhor_rede(h["ip"], indice)
        if achada is None:
            sem_rede += 1
            continue
        r = por_chave[achada]
        r["total"] += 1
        r["online"] += 1 if h.get("online") else 0
        tipo = h.get("tipo_sugerido") or "outro"
        r["por_tipo"][tipo] = r["por_tipo"].get(tipo, 0) + 1
    for u in unidades:
        for r in u["redes"]:
            r["por_tipo"] = dict(sorted(r["por_tipo"].items(), key=lambda kv: -kv[1]))
        u["dispositivos"] = sum(r["total"] for r in u["redes"])

    nomes = [fw["unidade"] for fw in fortigates] + list(pendentes)
    tuneis: dict[tuple[str, str], dict[str, Any]] = {}
    for fw in fortigates:
        for sla in fw.get("sdwan", []):
            destino = destino_do_sla(sla["sla"], nomes)
            if not destino or destino == fw["unidade"]:
                continue
            for m in sla["members"]:
                chave_t = (fw["unidade"], destino)
                atual = tuneis.get(chave_t)
                # Vale o melhor membro (o túnel "de pé"); os demais ficam na contagem
                if atual is None or (
                    m["status"] != "down"
                    and (atual["status"] == "down" or (m.get("latency_ms") or 1e9) < (atual.get("latency_ms") or 1e9))
                ):
                    tuneis[chave_t] = {"de": fw["unidade"], "para": destino, "iface": m["iface"], "latency_ms": m.get("latency_ms"),
                                       "status": m["status"], "membros": (atual or {}).get("membros", 0)}  # fmt: skip
                tuneis[chave_t]["membros"] += 1
                tuneis[chave_t]["caidos"] = tuneis[chave_t].get("caidos", 0) + (1 if m["status"] == "down" else 0)
    return {
        "unidades": unidades,
        "pendentes": list(pendentes),
        "sem_rede": sem_rede,
        "tuneis": sorted(tuneis.values(), key=lambda t: (t["de"], t["para"])),
    }


def layout_geral(topologia: dict[str, Any], largura: int = 760, altura: int = 360) -> dict[str, Any]:
    """Posições do mapa geral (SVG): unidades num círculo, destinos externos por fora.

    Returns:
        ``nos`` (nome, x, y, tipo: unidade|pendente|externo, ok) e ``arestas``
        (x1, y1, x2, y2, rótulo, status), prontos para o template desenhar.
    """
    unidades = [u["nome"] for u in topologia["unidades"]] + topologia["pendentes"]
    externos = sorted({t["para"] for t in topologia["tuneis"]} - set(unidades))
    cx, cy = largura / 2, altura / 2
    raio = min(largura, altura) / 2 - 60
    nos: dict[str, dict[str, Any]] = {}
    ok = {u["nome"]: u["ok"] for u in topologia["unidades"]}
    for i, nome in enumerate(unidades):
        ang = -math.pi / 2 + 2 * math.pi * i / max(len(unidades), 1)
        nos[nome] = {"nome": nome, "x": round(cx + raio * math.cos(ang)), "y": round(cy + raio * math.sin(ang)),
                     "tipo": "unidade" if nome in ok else "pendente", "ok": ok.get(nome, False)}  # fmt: skip
    for i, nome in enumerate(externos):
        x = round(largura * (i + 1) / (len(externos) + 1))
        nos[nome] = {"nome": nome, "x": x, "y": altura - 22, "tipo": "externo", "ok": True}

    # Ida e volta (Sede→Shopping e Shopping→Sede) viram uma aresta só, com o pior status
    pares: dict[frozenset[str], list[dict[str, Any]]] = {}
    for t in topologia["tuneis"]:
        if t["de"] in nos and t["para"] in nos:
            pares.setdefault(frozenset((t["de"], t["para"])), []).append(t)
    ordem = {"down": 0, "degraded": 1, "warn": 2, "up": 3}
    arestas = []
    for medidas in pares.values():
        a, b = nos[medidas[0]["de"]], nos[medidas[0]["para"]]
        latencias = [t["latency_ms"] for t in medidas if t.get("latency_ms") is not None]
        arestas.append(
            {
                "x1": a["x"], "y1": a["y"], "x2": b["x"], "y2": b["y"],
                "rotulo": f"{min(latencias):.0f} ms" if latencias else "sem resposta",
                "status": min((t["status"] for t in medidas), key=lambda s: ordem.get(s, 0)),
                "lx": round((a["x"] + b["x"]) / 2), "ly": round((a["y"] + b["y"]) / 2),
            }
        )  # fmt: skip
    return {"nos": list(nos.values()), "arestas": arestas, "largura": largura, "altura": altura}
