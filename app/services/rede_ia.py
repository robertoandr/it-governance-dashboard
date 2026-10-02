"""Classificação por IA dos hosts que as regras da página Rede não resolveram.

Fala com qualquer endpoint compatível com a API de chat da OpenAI (no servidor,
o OmniRoute). Desligado enquanto ``REDE_IA_URL`` e ``REDE_IA_KEY`` não estiverem
no ambiente — a página segue só com as regras.

Só vão para a IA hosts com algum sinal (portas, descrição SNMP, nome); o que
só responde a ping não tem o que analisar. Cada IP é enviado de novo apenas
quando os sinais mudam (``assinatura``), em lotes, numa thread em segundo
plano: a página nunca espera pela IA.

A descrição SNMP vem do próprio dispositivo; a resposta só é aceita dentro dos
tipos conhecidos e com textos curtos, então um texto malicioso no dispositivo
no máximo gera uma sugestão errada, que a pessoa revisa antes de cadastrar.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

import requests
import structlog
from flask import Flask

from itgov.api.v1.rede_descoberta import precisa_ia
from itgov.models.ativo import TIPO_LABELS

log = structlog.get_logger(__name__)

LOTE = 25
ESPERA_FALHA = 600  # segundos sem tentar de novo depois de uma falha
_ocupado = threading.Lock()  # um lote por vez
_falhou_em = 0.0

_PROMPT = """Você classifica dispositivos encontrados numa rede corporativa (lojas, shopping, fábrica, escritório).
Para cada host recebe: ip, portas TCP abertas, descrição SNMP (sysDescr), nome DNS ou do monitoramento.
Responda SOMENTE com JSON: {"hosts": [{"ip": "...", "tipo": "...", "nome": "...", "motivo": "..."}]}
- tipo: um destes valores: %(tipos)s. Use "outro" se os sinais não bastarem.
- nome: nome curto e descritivo do dispositivo (ex.: "Impressora Ricoh IM C2000"), até 60 caracteres.
- motivo: em até 12 palavras, qual sinal levou ao tipo.
Não invente: se não der para saber, tipo "outro" e diga por quê."""


def ia_ativa() -> bool:
    """Se há endpoint e chave configurados."""
    return bool(os.getenv("REDE_IA_URL") and os.getenv("REDE_IA_KEY"))


def assinatura(host: dict[str, Any]) -> str:
    """Resumo dos sinais do host; muda quando vale perguntar de novo."""
    sinais = [host.get("portas", ""), host.get("snmp_descr", ""), host.get("hostname", "")]
    return hashlib.sha256(json.dumps(sinais).encode()).hexdigest()[:32]


def _sinais(host: dict[str, Any]) -> dict[str, str]:
    return {
        "ip": host["ip"],
        "portas": host.get("portas", ""),
        "snmp": (host.get("snmp_descr") or "")[:300],
        "nome": host.get("hostname", ""),
    }


def classificar_lote(hosts: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Pergunta à IA o tipo e um nome para cada host.

    Args:
        hosts: Hosts no formato da página Rede.

    Returns:
        IP → ``{"tipo", "nome", "motivo"}``, só com respostas válidas.

    Raises:
        requests.RequestException: Falha de rede ou HTTP.
        ValueError: Resposta sem JSON utilizável.
    """
    base = os.environ["REDE_IA_URL"].rstrip("/")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['REDE_IA_KEY']}"},
        json={
            "model": os.getenv("REDE_IA_MODEL", "principal"),
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _PROMPT % {"tipos": ", ".join(sorted(TIPO_LABELS))}},
                {"role": "user", "content": json.dumps([_sinais(h) for h in hosts], ensure_ascii=False)},
            ],
        },
        timeout=90,
    )
    resp.raise_for_status()
    texto = resp.json()["choices"][0]["message"]["content"] or ""
    achado = re.search(r"\{.*\}", texto, re.S)
    if not achado:
        raise ValueError("resposta da IA sem JSON")
    pedidos = {h["ip"] for h in hosts}
    resultado: dict[str, dict[str, str]] = {}
    for item in json.loads(achado.group(0)).get("hosts", []):
        ip, tipo = str(item.get("ip", "")), str(item.get("tipo", ""))
        if ip in pedidos and tipo in TIPO_LABELS:
            resultado[ip] = {
                "tipo": tipo,
                "nome": str(item.get("nome", ""))[:60].strip(),
                "motivo": str(item.get("motivo", ""))[:120].strip(),
            }
    return resultado


def processar_em_segundo_plano(app: Flask, hosts: list[dict[str, Any]]) -> None:
    """Classifica, numa thread, os hosts pendentes (um lote por vez).

    Não faz nada se a IA estiver desligada, se já houver um lote rodando ou se
    a última tentativa falhou há pouco.
    """
    if not ia_ativa() or time.monotonic() - _falhou_em < ESPERA_FALHA:
        return
    if not _ocupado.acquire(blocking=False):
        return
    threading.Thread(target=_rodar, args=(app, hosts), daemon=True).start()


def _rodar(app: Flask, hosts: list[dict[str, Any]]) -> None:
    try:
        _processar(app, hosts)
    finally:
        _ocupado.release()


def _processar(app: Flask, hosts: list[dict[str, Any]]) -> None:
    global _falhou_em
    from app.extensions import db
    from app.models.rede import RedeVisto

    try:
        with app.app_context():
            vistos = {v.ip: v for v in RedeVisto.query.all()}
            pendentes = [
                h for h in hosts if precisa_ia(h) and (v := vistos.get(h["ip"])) and v.ia_assinatura != assinatura(h)
            ][:LOTE]
            if not pendentes:
                return
            respostas = classificar_lote(pendentes)
            agora = datetime.now(UTC)
            for h in pendentes:
                v = vistos[h["ip"]]
                r = respostas.get(h["ip"], {"tipo": "", "nome": "", "motivo": ""})
                v.ia_tipo, v.ia_nome, v.ia_motivo = r["tipo"], r["nome"], r["motivo"]
                v.ia_assinatura, v.ia_em = assinatura(h), agora
            db.session.commit()
            log.info("rede_ia.lote_classificado", pedidos=len(pendentes), respondidos=len(respostas))
    except (requests.RequestException, ValueError, KeyError) as exc:
        _falhou_em = time.monotonic()
        log.warning("rede_ia.lote_falhou", erro=str(exc))
