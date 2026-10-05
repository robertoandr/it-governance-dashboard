"""SNMP dos gravadores do CFTV (Conferência V2.0 cf7).

O admin informa IP e credenciais SNMP (v2c ou v3) de um gravador na página
/cftv e o dashboard grava no Zabbix:

- gravador já monitorado: troca o IP das interfaces e a interface SNMP;
- gravador que só existe na tag ``dvr`` das câmeras: cria o host no grupo
  ``CFTV/DVRs`` com o template "Generic by SNMP" (que já traz o ICMP) e a tag
  ``dvr`` com o nome do gravador, para as câmeras continuarem no mesmo card.

As senhas vão só para o Zabbix, como macros **secretas do host** — o dashboard
não guarda nem lê de volta. Senha em branco mantém a que já está lá. Ao fim,
pede ao Zabbix para checar os itens SNMP na hora ("check now").
"""

from __future__ import annotations

import re
import unicodedata
from ipaddress import IPv4Address
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, model_validator

from itgov.api.v1.cftv_monitoring import _zbx

log = structlog.get_logger(__name__)

GRUPO_GRAVADORES = "CFTV/DVRs"
TEMPLATE_NOVO = "Generic by SNMP"

# Macros do host referenciadas pela interface (mesmos nomes das globais)
MACRO_USUARIO = "{$CFTV.SNMPV3.USER}"
MACRO_AUTH = "{$CFTV.SNMPV3.AUTHPASS}"
MACRO_PRIV = "{$CFTV.SNMPV3.PRIVPASS}"
MACRO_COMMUNITY = "{$SNMP_COMMUNITY}"

TIPO_AGENTE, TIPO_SNMP = "1", "2"
MACRO_TEXTO, MACRO_SECRETA = "0", "1"
ITEM_SNMP = "20"
TASK_CHECK_NOW = "6"

# Rótulos na ordem dos códigos do Zabbix 7 (índice = código)
NIVEIS = ("noAuthNoPriv", "authNoPriv", "authPriv")
AUTH_PROTOCOLOS = ("MD5", "SHA1", "SHA224", "SHA256", "SHA384", "SHA512")
PRIV_PROTOCOLOS = ("DES", "AES128", "AES192", "AES256", "AES192C", "AES256C")


class ConfigSnmp(BaseModel):
    """IP e credenciais SNMP de um gravador, como vieram do formulário."""

    ip: IPv4Address
    porta: int = Field(default=161, ge=1, le=65535)
    versao: Literal["2", "3"]
    community: str = Field(default="", max_length=64)
    usuario: str = Field(default="", max_length=64)
    nivel: int = Field(default=2, ge=0, le=len(NIVEIS) - 1)
    auth_protocolo: int = Field(default=1, ge=0, le=len(AUTH_PROTOCOLOS) - 1)
    auth_senha: str = Field(default="", max_length=64)
    priv_protocolo: int = Field(default=1, ge=0, le=len(PRIV_PROTOCOLOS) - 1)
    priv_senha: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def _conferir(self) -> ConfigSnmp:
        if self.versao == "3" and not self.usuario.strip():
            raise ValueError("informe o usuário SNMPv3")
        # RFC 3414: chave derivada de senha com menos de 8 caracteres é recusada pelo net-snmp
        for nome, senha in (("autenticação", self.auth_senha), ("privacidade", self.priv_senha)):
            if senha and len(senha) < 8:
                raise ValueError(f"a senha de {nome} precisa de pelo menos 8 caracteres")
        return self

    def detalhes(self) -> dict[str, str]:
        """Campo ``details`` da interface SNMP (credenciais por macro do host)."""
        if self.versao == "2":
            return {"version": "2", "bulk": "1", "community": MACRO_COMMUNITY}
        return {
            "version": "3",
            "bulk": "1",
            "securityname": MACRO_USUARIO,
            "securitylevel": str(self.nivel),
            "authprotocol": str(self.auth_protocolo),
            "authpassphrase": MACRO_AUTH,
            "privprotocol": str(self.priv_protocolo),
            "privpassphrase": MACRO_PRIV,
            "contextname": "",
        }

    def macros(self) -> list[tuple[str, str, str]]:
        """Macros do host a gravar: (macro, valor, tipo). Senha vazia fica de fora (mantém)."""
        if self.versao == "2":
            return [(MACRO_COMMUNITY, self.community, MACRO_SECRETA)] if self.community else []
        saida = [(MACRO_USUARIO, self.usuario.strip(), MACRO_TEXTO)]
        if self.nivel >= 1 and self.auth_senha:
            saida.append((MACRO_AUTH, self.auth_senha, MACRO_SECRETA))
        if self.nivel == 2 and self.priv_senha:
            saida.append((MACRO_PRIV, self.priv_senha, MACRO_SECRETA))
        return saida


class Resultado(BaseModel):
    """O que foi feito no Zabbix."""

    hostid: str
    criado: bool
    itens_verificados: int
    senhas_faltando: list[str] = []


def nome_tecnico(gravador: str) -> str:
    """Nome técnico de host aceito pelo Zabbix (sem acento; só letras, números, espaço, ``.``, ``-`` e ``_``)."""
    sem_acento = unicodedata.normalize("NFKD", gravador).encode("ascii", "ignore").decode()
    limpo = re.sub(r"[^A-Za-z0-9 ._-]", "", sem_acento).strip()
    return (limpo or "gravador")[:120]


def _host_do_gravador(gravador: str) -> dict[str, Any] | None:
    # Host criado pelo dashboard leva a tag dvr; os antigos usam o próprio nome técnico
    for filtro in (
        {
            "tags": [
                {"tag": "dvr", "value": gravador, "operator": 1},
                {"tag": "subcategory", "value": "dvr", "operator": 1},
            ]
        },
        {"filter": {"host": [gravador]}},
    ):
        hosts = _zbx(
            "host.get",
            {
                "output": ["hostid", "host"],
                "selectInterfaces": ["interfaceid", "type", "main", "ip", "port"],
                "selectMacros": ["hostmacroid", "macro", "type"],
                **filtro,
            },
        )
        if hosts:
            return hosts[0]
    return None


def _criar_host(gravador: str, cfg: ConfigSnmp) -> str:
    grupos = _zbx("hostgroup.get", {"output": ["groupid"], "filter": {"name": [GRUPO_GRAVADORES]}})
    templates = _zbx("template.get", {"output": ["templateid"], "filter": {"name": [TEMPLATE_NOVO]}})
    if not grupos or not templates:
        raise RuntimeError(f"grupo {GRUPO_GRAVADORES} ou template {TEMPLATE_NOVO} não existe no Zabbix")
    criado = _zbx(
        "host.create",
        {
            "host": nome_tecnico(gravador),
            "name": gravador,
            "groups": [{"groupid": grupos[0]["groupid"]}],
            "templates": [{"templateid": templates[0]["templateid"]}],
            "tags": [
                {"tag": "subcategory", "value": "dvr"},
                {"tag": "dvr", "value": gravador},
                {"tag": "origem", "value": "dashboard"},
            ],
            "interfaces": [_interface_snmp(cfg, main=True)],
            "macros": [{"macro": m, "value": v, "type": t} for m, v, t in cfg.macros()],
        },
    )
    return str(criado["hostids"][0])


def _interface_snmp(cfg: ConfigSnmp, main: bool) -> dict[str, Any]:
    return {
        "type": TIPO_SNMP,
        "main": "1" if main else "0",
        "useip": "1",
        "ip": str(cfg.ip),
        "dns": "",
        "port": str(cfg.porta),
        "details": cfg.detalhes(),
    }


def _atualizar_host(host: dict[str, Any], cfg: ConfigSnmp) -> None:
    interfaces = host.get("interfaces") or []
    snmp = next((i for i in interfaces if i["type"] == TIPO_SNMP and i.get("main") == "1"), None)
    if snmp:
        _zbx(
            "hostinterface.update",
            {"interfaceid": snmp["interfaceid"], "ip": str(cfg.ip), "port": str(cfg.porta), "details": cfg.detalhes()},
        )
    else:
        _zbx("hostinterface.create", {"hostid": host["hostid"], **_interface_snmp(cfg, main=True)})
    # O ping usa a interface principal de outro tipo (agente) nos hosts antigos: acompanha o IP
    for i in interfaces:
        if i["type"] != TIPO_SNMP and i.get("ip") != str(cfg.ip):
            _zbx("hostinterface.update", {"interfaceid": i["interfaceid"], "ip": str(cfg.ip)})

    existentes = {m["macro"]: m["hostmacroid"] for m in host.get("macros") or []}
    for macro, valor, tipo in cfg.macros():
        if macro in existentes:
            _zbx("usermacro.update", {"hostmacroid": existentes[macro], "value": valor, "type": tipo})
        else:
            _zbx("usermacro.create", {"hostid": host["hostid"], "macro": macro, "value": valor, "type": tipo})


def _senhas_faltando(cfg: ConfigSnmp, macros_do_host: set[str]) -> list[str]:
    """Credenciais que nem vieram no formulário nem existem no host (cairiam nas globais)."""
    gravadas = macros_do_host | {m for m, _v, _t in cfg.macros()}
    if cfg.versao == "2":
        exigidas = {MACRO_COMMUNITY: "community"}
    else:
        exigidas = {MACRO_AUTH: "senha de autenticação"} if cfg.nivel >= 1 else {}
        if cfg.nivel == 2:
            exigidas[MACRO_PRIV] = "senha de privacidade"
    return [rotulo for macro, rotulo in exigidas.items() if macro not in gravadas]


def verificar_agora(hostid: str) -> int:
    """Pede ao Zabbix a checagem imediata dos itens SNMP do host.

    Returns:
        Quantos itens foram agendados.
    """
    itens = _zbx(
        "item.get",
        {"output": ["itemid"], "hostids": [hostid], "filter": {"type": ITEM_SNMP, "status": "0"}, "limit": 20},
    )
    if itens:
        _zbx("task.create", [{"type": TASK_CHECK_NOW, "request": {"itemid": i["itemid"]}} for i in itens])
    return len(itens)


def aplicar(gravador: str, cfg: ConfigSnmp) -> Resultado:
    """Grava IP e SNMP do gravador no Zabbix, criando o host se ele ainda não existir.

    Args:
        gravador: Nome do gravador (nome técnico do host ou valor da tag ``dvr`` das câmeras).
        cfg: IP e credenciais validados.

    Returns:
        Host afetado, se foi criado, itens agendados e credenciais que ficaram faltando.

    Raises:
        RuntimeError: O Zabbix recusou a operação ou falta grupo/template.
    """
    host = _host_do_gravador(gravador)
    if host is None:
        hostid, criado, macros_do_host = _criar_host(gravador, cfg), True, set()
    else:
        _atualizar_host(host, cfg)
        hostid, criado = host["hostid"], False
        macros_do_host = {m["macro"] for m in host.get("macros") or []}
    try:
        verificados = verificar_agora(hostid)
    except RuntimeError as exc:
        # Itens recém-criados podem ainda não aceitar "check now": o Zabbix coleta no ciclo normal
        log.warning("cftv_snmp.check_now_falhou", hostid=hostid, erro=str(exc)[:200])
        verificados = 0
    log.info("cftv_snmp.aplicado", gravador=gravador, hostid=hostid, criado=criado, versao=cfg.versao)
    return Resultado(
        hostid=hostid,
        criado=criado,
        itens_verificados=verificados,
        senhas_faltando=_senhas_faltando(cfg, macros_do_host),
    )


def status_snmp(interfaces: list[dict[str, Any]], macros: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Situação SNMP de um host a partir das interfaces e macros do ``host.get``.

    Returns:
        ``None`` sem interface SNMP; senão versão, status (``ok``, ``falha``,
        ``aguardando``), erro do Zabbix e o que preencher no formulário.
    """
    snmp = next((i for i in interfaces if i.get("type") == TIPO_SNMP), None)
    if snmp is None:
        return None
    det = snmp.get("details") or {}
    textos = {m["macro"]: m.get("value", "") for m in macros if m.get("type") == MACRO_TEXTO}
    return {
        "versao": det.get("version", ""),
        "status": {"1": "ok", "2": "falha"}.get(str(snmp.get("available")), "aguardando"),
        "erro": snmp.get("error", ""),
        "porta": snmp.get("port", "161"),
        "usuario": textos.get(MACRO_USUARIO, ""),
        "nivel": int(det.get("securitylevel") or 2),
        "auth_protocolo": int(det.get("authprotocol") or 0),
        "priv_protocolo": int(det.get("privprotocol") or 0),
        "macros_host": sorted(m["macro"] for m in macros),
    }
