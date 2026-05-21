#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#   Setup de Credenciais Zabbix API (v3 - HTTPS aware)
#   Projeto: Dashboard Governança TI — Grupo Gadens
# ═══════════════════════════════════════════════════════════════

CONFIG_FILE="/opt/it-gov-dashboard/cftv-setup/.zabbix_config.json"

echo "═══════════════════════════════════════════════════════════"
echo "  🔐 Setup de Credenciais Zabbix API (v3 - HTTPS)"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ─── [1/6] Detectar URL (HTTPS prioritário) ────────────────────
echo "[1/6] 📡 Detectando URL da API Zabbix..."

# Ordem: HTTPS preferido > HTTP fallback
declare -a URLS_TO_TEST=(
    "https://172.29.2.11/zabbix/api_jsonrpc.php"
    "https://noc.grupogadens.com.br/zabbix/api_jsonrpc.php"
    "https://localhost/zabbix/api_jsonrpc.php"
    "http://172.29.2.11:8080/zabbix/api_jsonrpc.php"
)

DEFAULT_URL=""
for try_url in "${URLS_TO_TEST[@]}"; do
    echo -n "      Testando $try_url ... "
    HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 \
                -X POST -H "Content-Type: application/json-rpc" \
                -d '{"jsonrpc":"2.0","method":"apiinfo.version","params":[],"id":1}' \
                "$try_url" 2>/dev/null)
    if [[ "$HTTP_CODE" == "200" ]]; then
        echo "✅ OK"
        [[ -z "$DEFAULT_URL" ]] && DEFAULT_URL="$try_url"
    else
        echo "❌ ($HTTP_CODE)"
    fi
done

if [[ -z "$DEFAULT_URL" ]]; then
    echo ""
    echo "   ❌ Nenhuma URL respondeu 200. Digite manualmente."
    DEFAULT_URL="https://172.29.2.11/zabbix/api_jsonrpc.php"
fi

echo ""
echo "   URL escolhida: $DEFAULT_URL"
read -p "   Pressione ENTER para aceitar ou digite outra: " API_URL
API_URL="${API_URL:-$DEFAULT_URL}"

# Detecta se é HTTPS pra setar verify_ssl
if [[ "$API_URL" == https://* ]]; then
    USE_HTTPS=true
    echo "   🔒 HTTPS detectado — certificado será aceito sem validação estrita"
else
    USE_HTTPS=false
fi
echo ""

# ─── [2/6] Versão da API ───────────────────────────────────────
echo "[2/6] 🔍 Verificando versão do Zabbix..."

VERSION_RESP=$(curl -sk --max-time 10 \
    -X POST -H "Content-Type: application/json-rpc" \
    -d '{"jsonrpc":"2.0","method":"apiinfo.version","params":[],"id":1}' \
    "$API_URL")

ZBX_VERSION=$(echo "$VERSION_RESP" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('result', 'desconhecida'))
except:
    print('ERRO')
" 2>/dev/null)

if [[ "$ZBX_VERSION" == "ERRO" || -z "$ZBX_VERSION" ]]; then
    echo "   ⚠️  Não consegui ler versão. Resposta:"
    echo "      ${VERSION_RESP:0:200}"
    echo ""
    read -p "   Continuar mesmo assim? [s/N]: " CONT
    [[ "$CONT" != "s" && "$CONT" != "S" ]] && exit 1
else
    echo "   ✅ Zabbix versão: $ZBX_VERSION"
fi
echo ""

# ─── [3/6] Usuário ─────────────────────────────────────────────
echo "[3/6] 👤 Usuário Zabbix"
read -p "      Usuário [Admin]: " ZBX_USER
ZBX_USER="${ZBX_USER:-Admin}"
echo "   ✅ Usuário: $ZBX_USER"
echo ""

# ─── [4/6] Senha ───────────────────────────────────────────────
echo "[4/6] 🔑 Senha do usuário '$ZBX_USER'"
echo -n "      Digite a senha (não aparecerá): "
read -rs ZBX_PASS
echo ""
echo ""

if [[ -z "$ZBX_PASS" ]]; then
    echo "   ❌ Senha vazia. Abortando."
    exit 1
fi
echo "   ✅ Senha recebida (${#ZBX_PASS} caracteres)"
echo ""

# ─── [5/6] Testar autenticação ─────────────────────────────────
echo "[5/6] 🧪 Testando autenticação na API..."

LOGIN_PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'jsonrpc': '2.0',
    'method': 'user.login',
    'params': {'username': sys.argv[1], 'password': sys.argv[2]},
    'id': 1
}))
" "$ZBX_USER" "$ZBX_PASS")

RESPONSE=$(curl -sk --max-time 10 \
    -X POST \
    -H "Content-Type: application/json-rpc" \
    -d "$LOGIN_PAYLOAD" \
    "$API_URL")

PARSE_RESULT=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    if 'error' in data:
        err = data['error']
        msg = err.get('data') or err.get('message') or 'unknown'
        print('ERROR|' + str(msg))
    elif 'result' in data:
        print('OK|' + data['result'])
    else:
        print('UNKNOWN|' + str(data)[:200])
except Exception as e:
    print('PARSE_FAIL|' + str(e))
")

STATUS="${PARSE_RESULT%%|*}"
DETAIL="${PARSE_RESULT#*|}"

case "$STATUS" in
    OK)
        echo "   ✅ Autenticação bem-sucedida"
        echo "   ✅ Token: ${DETAIL:0:16}..."
        TOKEN="$DETAIL"
        ;;
    ERROR)
        echo "   ❌ Falha na autenticação: $DETAIL"
        echo ""
        echo "   Resposta completa:"
        echo "   $RESPONSE"
        unset ZBX_PASS
        exit 1
        ;;
    *)
        echo "   ❌ Erro: $PARSE_RESULT"
        echo "   Resposta: ${RESPONSE:0:300}"
        unset ZBX_PASS
        exit 1
        ;;
esac
echo ""

# ─── [6/6] Salvar config ───────────────────────────────────────
echo "[6/6] 💾 Salvando configuração..."
python3 <<PYEOF
import json
cfg = {
    "api_url": "$API_URL",
    "user": "$ZBX_USER",
    "password": $(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$ZBX_PASS"),
    "zabbix_version": "$ZBX_VERSION",
    "verify_ssl": False,
    "use_https": $([ "$USE_HTTPS" = true ] && echo "true" || echo "false"),   ← ESTA LINHA
    "_comment": "Gerado por 00_setup_zabbix_config.sh - NÃO COMMITAR"
}

PYEOF

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "   ❌ Falha ao criar arquivo!"
    unset ZBX_PASS
    exit 1
fi

chown zabbix:zabbix "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"

echo "   ✅ Arquivo: $CONFIG_FILE"
echo "   ✅ Owner:   zabbix:zabbix"
echo "   ✅ Permissão: 600"
echo ""

# Logout
curl -sk --max-time 5 \
    -X POST \
    -H "Content-Type: application/json-rpc" \
    -H "Authorization: Bearer $TOKEN" \
    -d '{"jsonrpc":"2.0","method":"user.logout","params":[],"id":1}' \
    "$API_URL" > /dev/null

echo "📋 Verificação final:"
ls -la "$CONFIG_FILE"
echo ""
echo "   Conteúdo (senha mascarada):"
python3 -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
cfg['password'] = '***MASKED*** (' + str(len(cfg['password'])) + ' chars)'
for k, v in cfg.items():
    print(f'      {k}: {v}')
"

unset ZBX_PASS LOGIN_PAYLOAD RESPONSE TOKEN

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✅ CONFIGURAÇÃO CONCLUÍDA"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Próximo passo: atualizar script Python pra suportar HTTPS"
echo ""
