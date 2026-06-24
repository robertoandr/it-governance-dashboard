"""Servico de verificacao DNS para seguranca de e-mail (SPF, DMARC, DKIM).

KPI-EMAIL-02: valida a presenca e configuracao dos registros DNS de protecao
de e-mail para o dominio do tenant M365.

Dominio lido de M365_TENANT_DOMAIN env var. Se vazio, retorna dados indisponíveis.
Usa dnspython para consultas DNS (pacote dnspython>=2.6).
"""

from __future__ import annotations

import os
import re

import structlog

log = structlog.get_logger(__name__)


def _get_domain() -> str:
    return os.getenv("M365_TENANT_DOMAIN", "").strip()


def check_spf(domain: str) -> dict:
    """Verifica registro SPF no DNS do domínio.

    Args:
        domain: Domínio a verificar (ex: empresa.com.br).

    Returns:
        dict com chaves: found (bool), record (str | None).
    """
    try:
        import dns.resolver

        answers = dns.resolver.resolve(domain, "TXT")
        for rdata in answers:
            record = str(rdata).strip('"').strip()
            if record.startswith("v=spf1"):
                log.info("dns_check.spf_found", domain=domain)
                return {"found": True, "record": record}
        log.info("dns_check.spf_not_found", domain=domain)
        return {"found": False, "record": None}
    except Exception as exc:
        log.warning("dns_check.spf_failed", domain=domain, error=str(exc))
        return {"found": False, "record": None}


def check_dmarc(domain: str) -> dict:
    """Verifica registro DMARC no DNS do domínio.

    Consulta TXT em _dmarc.<domain>. Extrai a política (p=) do registro.

    Args:
        domain: Domínio base (ex: empresa.com.br).

    Returns:
        dict com chaves: found (bool), policy (str | None), record (str | None).
    """
    dmarc_domain = f"_dmarc.{domain}"
    try:
        import dns.resolver

        answers = dns.resolver.resolve(dmarc_domain, "TXT")
        for rdata in answers:
            record = str(rdata).strip('"').strip()
            if "v=DMARC1" in record:
                match = re.search(r"p=([^;]+)", record)
                policy = match.group(1).strip() if match else None
                log.info("dns_check.dmarc_found", domain=domain, policy=policy)
                return {"found": True, "policy": policy, "record": record}
        log.info("dns_check.dmarc_not_found", domain=domain)
        return {"found": False, "policy": None, "record": None}
    except Exception as exc:
        log.warning("dns_check.dmarc_failed", domain=dmarc_domain, error=str(exc))
        return {"found": False, "policy": None, "record": None}


def check_dkim(domain: str, selector: str = "selector1") -> dict:
    """Verifica registro DKIM no DNS do domínio.

    Consulta TXT em <selector>._domainkey.<domain>.

    Args:
        domain: Domínio base (ex: empresa.com.br).
        selector: Seletor DKIM (padrão: selector1, usado pelo M365).

    Returns:
        dict com chaves: found (bool), selector (str).
    """
    dkim_domain = f"{selector}._domainkey.{domain}"
    try:
        import dns.resolver

        answers = dns.resolver.resolve(dkim_domain, "TXT")
        for rdata in answers:
            record = str(rdata).strip('"').strip()
            if record:
                log.info("dns_check.dkim_found", domain=domain, selector=selector)
                return {"found": True, "selector": selector}
        log.info("dns_check.dkim_not_found", domain=domain, selector=selector)
        return {"found": False, "selector": selector}
    except Exception as exc:
        log.warning("dns_check.dkim_failed", domain=dkim_domain, error=str(exc))
        return {"found": False, "selector": selector}


def get_email_security_summary(domain: str) -> dict:
    """Retorna resumo consolidado de segurança de e-mail via DNS.

    Args:
        domain: Domínio do tenant M365.

    Returns:
        dict com chaves: domain, available (bool), spf, dmarc, dkim.
        Se domain estiver vazio, available=False e demais campos são None.
    """
    if not domain:
        log.warning("dns_check.domain_not_configured")
        return {
            "domain": None,
            "available": False,
            "spf": None,
            "dmarc": None,
            "dkim": None,
        }

    spf = check_spf(domain)
    dmarc = check_dmarc(domain)
    dkim = check_dkim(domain)

    log.info(
        "dns_check.summary",
        domain=domain,
        spf_found=spf["found"],
        dmarc_found=dmarc["found"],
        dkim_found=dkim["found"],
    )

    return {
        "domain": domain,
        "available": True,
        "spf": spf,
        "dmarc": dmarc,
        "dkim": dkim,
    }
