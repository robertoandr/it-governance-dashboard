"""
Diagnóstico da API Zabbix — valida conectividade e autenticação.

SECURITY:
    Por padrão, valida certificados TLS (ZBX_VERIFY_TLS=true).
    Em ambientes de laboratório com CA interna, é possível:
      - Apontar para CA bundle: export ZBX_CA_BUNDLE=/path/to/ca.pem
      - Desabilitar (NÃO recomendado em produção):
          export ZBX_VERIFY_TLS=false
    Quando desabilitado, um aviso explícito é emitido no stderr.

USAGE:
    python scripts/diag_zbx.py

ENV VARS:
    ZABBIX_URL        URL da API (obrigatório)
    ZABBIX_TOKEN      Token de autenticação (ou variantes)
    ZBX_VERIFY_TLS    "true" (default) | "false"
    ZBX_CA_BUNDLE     Caminho para CA bundle customizado (opcional)
    ZBX_TIMEOUT       Timeout em segundos (default: 10)
"""

import json
import os
import sys
import warnings

import requests
import urllib3


# ─────────────────────────────────────────────────────────
# Configuração de segurança TLS
# ─────────────────────────────────────────────────────────
def _resolve_tls_verify():
    """Resolve a configuração de validação TLS de forma segura.

    Returns:
        bool | str: True (padrão CAs do sistema), str (path do CA bundle),
                    ou False (apenas em laboratório, com aviso).
    """
    ca_bundle = os.getenv("ZBX_CA_BUNDLE", "").strip()
    if ca_bundle:
        if not os.path.isfile(ca_bundle):
            print(f"⚠️  ZBX_CA_BUNDLE aponta para arquivo inexistente: {ca_bundle}", file=sys.stderr)
            sys.exit(2)
        return ca_bundle

    verify_flag = os.getenv("ZBX_VERIFY_TLS", "true").strip().lower()
    if verify_flag in ("false", "0", "no", "off"):
        warnings.warn(
            "⚠️  Validação TLS DESABILITADA (ZBX_VERIFY_TLS=false). "
            "Uso apenas em laboratório controlado. NÃO USE EM PRODUÇÃO.",
            stacklevel=2,
        )
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return False
    return True


TLS_VERIFY = _resolve_tls_verify()
TIMEOUT = int(os.getenv("ZBX_TIMEOUT", "10"))


# ─────────────────────────────────────────────────────────
# Carregamento do .env
# ─────────────────────────────────────────────────────────
ENV_PATH = os.getenv("ZBX_ENV_FILE", "/opt/it-gov-dashboard/.env")
env_vars = {}

try:
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                env_vars[k] = v
                os.environ[k] = v
except FileNotFoundError:
    print(f"❌ Arquivo .env não encontrado em: {ENV_PATH}", file=sys.stderr)
    sys.exit(1)

print("Variaveis encontradas no .env:")
for k, v in env_vars.items():
    masked = v[:6] + "***" if any(s in k.upper() for s in ["TOKEN", "PASS", "SECRET", "KEY"]) else v[:30]
    print(f"   {k} = {masked}")


# ─────────────────────────────────────────────────────────
# Resolução de token
# ─────────────────────────────────────────────────────────
token = None
for cand in [
    "ZABBIX_TOKEN",
    "ZABBIX_API_TOKEN",
    "ZBX_TOKEN",
    "ZABBIX_AUTH_TOKEN",
    "API_TOKEN",
    "TOKEN",
]:
    if env_vars.get(cand):
        token = env_vars[cand]
        print(f"\nToken encontrado em: {cand}")
        break

url = env_vars.get("ZABBIX_URL")

if not token:
    print("\nERRO: nenhum token encontrado no .env", file=sys.stderr)
    raise SystemExit(1)

if not url:
    print("\nERRO: ZABBIX_URL não definida no .env", file=sys.stderr)
    raise SystemExit(1)


# ─────────────────────────────────────────────────────────
# Testes de conectividade
# ─────────────────────────────────────────────────────────
print(f"\nTLS verify: {TLS_VERIFY!r} | Timeout: {TIMEOUT}s")
print(f"\nTestando {url} ...")

r = requests.post(
    url,
    json={
        "jsonrpc": "2.0",
        "method": "apiinfo.version",
        "params": {},
        "id": 1,
    },
    verify=TLS_VERIFY,
    timeout=TIMEOUT,
).json()
print(f"   Versao Zabbix: {r.get('result', 'ERRO')}")

print("\nTestando auth via Bearer header...")
r = requests.post(
    url,
    json={
        "jsonrpc": "2.0",
        "method": "host.get",
        "params": {"countOutput": True},
        "id": 2,
    },
    headers={
        "Content-Type": "application/json-rpc",
        "Authorization": f"Bearer {token}",
    },
    verify=TLS_VERIFY,
    timeout=TIMEOUT,
).json()

if "result" in r:
    print(f"   OK! Total de hosts: {r['result']}")
else:
    print(f"   ERRO: {json.dumps(r.get('error', {}))}")
    sys.exit(1)
