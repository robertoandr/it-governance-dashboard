"""Padronização de senha dos equipamentos CFTV (câmeras/gravadores Intelbras/Dahua).

Troca a senha de um equipamento para a credencial **padrão** usando a credencial
que ele aceita hoje — a que o coletor ``cftv_model_collector`` gravou na tag
``cftv_cred``. Nunca "chuta" senha: só age quando já temos acesso, para não disparar
o bloqueio de conta das Intelbras. Só Intelbras/Dahua (CGI) por enquanto; outros
fabricantes devolvem ``nao_suportado`` (fazer no console).

A ação é disparada pela fila de aprovação (ver ``app.views.dashboards`` e
``app.services.aprovacoes``): quem não é super admin só pede; o super admin aprova.
"""

from __future__ import annotations

import ipaddress
import os

import httpx
import requests
import structlog

from itgov.services.intelbras_api import AcessoNegadoError, trocar_senha

log = structlog.get_logger(__name__)

# Chaves da tag ``cftv_cred`` → par de variáveis de ambiente (igual ao coletor de modelos).
_CRED_ENV: dict[str, tuple[str, str]] = {
    "padrao": ("CFTV_CAM_PADRAO_USER", "CFTV_CAM_PADRAO_PASS"),
    "centro": ("CFTV_CAM_CENTRO_USER", "CFTV_CAM_CENTRO_PASS"),
    "triunfo": ("CFTV_CAM_USER", "CFTV_CAM_TRIUNFO_PASS"),
    "triunfo2": ("CFTV_CAM_USER", "CFTV_CAM_TRIUNFO_PASS2"),
    "intelbras": ("INTELBRAS_HTTP_USER", "INTELBRAS_HTTP_PASSWORD"),
}

# Fabricantes com troca de senha automática (CGI Intelbras/Dahua).
_VENDORS_CGI = {"intelbras", "dahua"}


def _cred(nome: str) -> tuple[str, str] | None:
    """Usuário e senha de uma credencial nomeada, lidos do ambiente (``None`` se faltar)."""
    par = _CRED_ENV.get(nome)
    if not par:
        return None
    usuario, senha = os.getenv(par[0], ""), os.getenv(par[1], "")
    return (usuario, senha) if usuario and senha else None


def padronizar_senha(host: str, ip: str, vendor: str, cred_atual: str) -> dict[str, str]:
    """Troca a senha do equipamento para a credencial padrão.

    Args:
        host: Nome técnico do host no Zabbix (para a atualização da tag).
        ip: IP do equipamento.
        vendor: Fabricante (tag ``vendor``).
        cred_atual: Chave da credencial que o equipamento aceita hoje (tag ``cftv_cred``).

    Returns:
        ``{"status": ..., "detalhe": ...}`` com ``status`` em ``ok`` | ``ja_padrao`` |
        ``recusada`` | ``nao_suportado`` | ``sem_cred`` | ``sem_padrao`` | ``erro``.
    """
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return {"status": "erro", "detalhe": f"IP inválido: '{ip}'"}
    padrao = _cred("padrao")
    if not padrao:
        return {"status": "sem_padrao", "detalhe": "credencial padrão não configurada no servidor"}
    if cred_atual == "padrao":
        return {"status": "ja_padrao", "detalhe": "o equipamento já usa a credencial padrão"}
    atual = _cred(cred_atual)
    if not atual:
        return {
            "status": "sem_cred",
            "detalhe": f"credencial atual ('{cred_atual or '—'}') não configurada no servidor",
        }
    if vendor.strip().lower() not in _VENDORS_CGI:
        return {"status": "nao_suportado", "detalhe": f"troca automática indisponível para '{vendor}' — usar o console"}

    usuario, senha_atual = atual
    _, senha_nova = padrao
    try:
        trocar_senha(ip, usuario, senha_atual, senha_nova)
    except AcessoNegadoError:
        log.warning("cftv_padronizar_recusada", host=host, ip=ip, cred_atual=cred_atual)
        return {"status": "recusada", "detalhe": "a credencial atual não foi aceita (401) — nada mudou"}
    except httpx.HTTPError as exc:
        log.warning("cftv_padronizar_falhou", host=host, ip=ip, erro=str(exc))
        return {"status": "erro", "detalhe": f"o equipamento não respondeu ou recusou a troca: {exc}"}

    _marcar_padrao_no_zabbix(host)
    log.info("cftv_padronizar_ok", host=host, ip=ip, cred_anterior=cred_atual)
    return {"status": "ok", "detalhe": "senha padronizada com sucesso"}


def _marcar_padrao_no_zabbix(host: str) -> None:
    """Melhor esforço: marca ``cftv_cred=padrao`` e remove ``cftv_cred_recusada`` no host."""
    try:
        from itgov.api.v1.cftv_monitoring import _zbx

        achados = _zbx("host.get", {"output": ["hostid"], "selectTags": "extend", "filter": {"host": [host]}})
        if not achados:
            return
        alvo = achados[0]
        tags = [t for t in alvo.get("tags", []) if t["tag"] not in ("cftv_cred", "cftv_cred_recusada")]
        tags.append({"tag": "cftv_cred", "value": "padrao"})
        _zbx("host.update", {"hostid": alvo["hostid"], "tags": tags})
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
        log.warning("cftv_padronizar_tag_falhou", host=host, erro=str(exc))
