# [INFRA] Publicação externa do IT Governance Dashboard + Hardening FortiGate

**Severidade:** 🔴 Alta (segurança) + 🟡 Média (publicação app)
**Solicitante:** Roberto Andrade
**Data:** 2026-06-07
**Domínio afetado:** `noc.grupogadens.com.br`
**IP público:** 189.112.203.34
**Backend:** `172.29.2.11:5000`

---

## 🎯 Resumo Executivo

O FortiGate está expondo sua **interface administrativa** (`https://noc.grupogadens.com.br/`)
na WAN pública, ao invés de fazer proxy reverso para a aplicação IT Governance Dashboard
que está rodando saudável em `172.29.2.11:5000`.

Isso gera **dois problemas simultâneos**:

1. **Risco de segurança:** GUI de administração do firewall acessível da internet
2. **App não publicada:** dashboard inacessível externamente apesar de funcional

---

## 🔬 Evidências Técnicas

### Comportamento atual

| Endpoint | Status | Server | Body |
|----------|--------|--------|------|
| `https://noc.grupogadens.com.br/` (GET)  | 200 | FortiGate | SPA admin do FortiOS |
| `https://noc.grupogadens.com.br/` (HEAD) | 405 | FortiGate | proteção nativa |
| `https://noc.grupogadens.com.br/` (POST) | 200 | FortiGate | redireciona p/ login GUI |
| `http://172.29.2.11:5000/` (interno)     | 200 | gunicorn  | dashboard funcional ✅ |

### Artefatos que confirmam ser GUI do FortiGate

- `<title>FortiGate</title>` no HTML
- Componente Angular `<fos-root>` (FortiOS root)
- Endpoint interno `/api/v2/monitor/web-ui/extend-session`
- Certificado TLS: `CN=FortiGate, O=Fortinet Ltd.` (self-signed)

### Comandos de verificação (read-only)

```bash
curl -k -s https://noc.grupogadens.com.br/ | grep -E "(title|fos-root)"
curl -k -sI https://noc.grupogadens.com.br/ | grep -i server
echo | openssl s_client -connect noc.grupogadens.com.br:443 2>/dev/null \
  | openssl x509 -noout -subject -issuer
```

---

## 🛠️ Ações Solicitadas

### 🔴 PRIORIDADE 1 — Desabilitar admin HTTPS na WAN

**Risco mitigado:** exposição de GUI admin permite brute-force, fingerprinting
de versão e exploração de CVEs conhecidos (CVE-2024-21762, CVE-2024-55591,
CVE-2022-40684).

**GUI:**
```
System → Settings → Administration Settings
  ☐ HTTPS: desmarcar interface "WAN"
  OU
  ☑ HTTPS port: alterar para porta não-padrão (ex: 8443)
  + restringir trusted hosts a IPs específicos da equipe de infra
```

**CLI:**
```
config system interface
    edit <wan-interface-name>
        set allowaccess ping
    next
end
```

⚠️ **Pré-requisito:** garantir caminho alternativo de admin (VPN, mgmt interface,
console serial) antes de executar.

---

### 🟡 PRIORIDADE 2 — Criar Virtual IP

**GUI:** `Policy & Objects → Virtual IPs → Create New`

```
Name:           VIP-itgov-443
Interface:      <wan-interface>
External IP:    189.112.203.34
Mapped IP:      172.29.2.11
Port Forward:   Enabled
  External port: 443
  Mapped port:   5000
Protocol:       TCP
```

**CLI:**
```
config firewall vip
    edit "VIP-itgov-443"
        set extip 189.112.203.34
        set mappedip "172.29.2.11"
        set extintf "<wan-interface>"
        set portforward enable
        set extport 443
        set mappedport 5000
        set protocol tcp
    next
end
```

---

### 🟡 PRIORIDADE 3 — Firewall Policy

**GUI:** `Policy & Objects → Firewall Policy → Create New`

```
Name:           WAN-to-ITGov-Dashboard
Incoming:       <wan-interface>
Outgoing:       <interface-do-172.29.2.11>
Source:         all
Destination:    VIP-itgov-443
Service:        HTTPS
Action:         ACCEPT
NAT:            OFF
SSL Inspection: no-inspection
Log:            All Sessions
Status:         Enabled
```

---

### 🟢 PRIORIDADE 4 (opcional) — SSL Offloading

```
VIP-itgov-443 → Edit
  ☑ Enable SSL Offloading
  Certificate: <cert do domínio noc.grupogadens.com.br>
  Mapped port: 5000  (HTTP plain do FortiGate para o app)
```

**Trade-off:**
- ✅ Cert válido sem warning no browser
- ✅ Gerenciamento centralizado de certs
- ⚠️ Tráfego FortiGate→app em HTTP plain (aceitável em LAN segura)

---

## 🧪 Critérios de Validação Pós-Mudança

```bash
# 1. GUI admin NÃO deve mais responder em 443
curl -k -s https://noc.grupogadens.com.br/ | grep -i fortigate
# esperado: vazio

# 2. App deve responder
curl -k -s https://noc.grupogadens.com.br/ | grep -i "governança"
# esperado: HTML do dashboard

# 3. Header Server
curl -k -sI https://noc.grupogadens.com.br/ | grep -i server
# esperado: Server: gunicorn

# 4. Health check
curl -k -s https://noc.grupogadens.com.br/api/health
# esperado: {"status": "healthy", ...}
```

---

## 📞 Contato

- **App owner:** Roberto Andrade
- **Backend:** `zadmin@172.29.2.11:/home/zabbix/projects/it-governance-dashboard`
- **Repositório:** `robertoandr/it-governance-dashboard`

App 100% saudável no backend. Aguardando apenas a publicação via FortiGate.
