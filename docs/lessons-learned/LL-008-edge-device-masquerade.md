# LL-008 — Edge devices podem responder como se fossem sua aplicação

**Data:** 2026-06-07
**Contexto:** `https://noc.grupogadens.com.br/` retornava HTML com status 200, mas era a GUI admin do FortiGate, não o dashboard.

## 🔴 Problema

Edge devices (firewalls, load balancers, WAFs, CDNs) podem:

- Responder com **status 200 + HTML completo** sem ser sua app
- Apresentar **certificado TLS** aceito pelo browser (válido ou self-signed)
- Interceptar requisições **silenciosamente** sem deixar rastro óbvio
- Aplicar **cache, WAF, rate limit** que alteram comportamento da app
- Retornar **erros HTTP que parecem do backend** mas são do edge

## 🧠 Caso Concreto

FortiGate publicando admin GUI em `:443` da WAN ao invés de fazer DNAT
para o app interno. Sintomas observados:

| Endpoint | Status | Aparência | Realidade |
|----------|--------|-----------|-----------|
| `/` GET  | 200 + HTML | "parece" um app funcionando | SPA do FortiOS |
| `/` HEAD | 405 | "parece" método não permitido pelo Flask | proteção nativa FortiGate |
| `/` POST | 200 + HTML | "parece" form de login do app | login GUI do FortiGate |
| `/gov/` GET | 200 + HTML | "parece" rota do app | mesma SPA (ignora path) |

**Nenhuma dessas respostas tocou o gunicorn.**

## 🔍 Sinais para Identificar Edge Device Mascarado

### 1. Inspecionar artefatos únicos no HTML

```bash
curl -k --compressed -s https://target/ | grep -iE "(fortigate|cloudflare|f5|nginx|fos-root)"
curl -k --compressed -s https://target/ | grep -E "(<title>|name=\"generator\")"
```

### 2. Comportamento assimétrico entre métodos HTTP

Apps Flask/Django/Express têm padrões consistentes.
Edge devices têm respostas **assimétricas** por método:

```bash
for m in GET HEAD POST OPTIONS; do
  echo -n "$m: "
  curl -k -s -o /dev/null -w "%{http_code}" -X $m https://target/
  echo
done
```

### 3. Certificado TLS revela o edge

```bash
echo | openssl s_client -connect target:443 -servername target 2>/dev/null \
  | openssl x509 -noout -subject -issuer
```

| CN/Issuer | Significado |
|-----------|-------------|
| `CN=FortiGate, O=Fortinet Ltd.` | Admin do firewall exposto |
| `CN=Kubernetes Ingress Controller Fake Certificate` | Ingress sem TLS configurado |
| `O=Cloudflare, Inc.` | Cloudflare terminando TLS |
| `CN=R10, O=Let's Encrypt` | Cert real do domínio ✅ |

### 4. Endpoints proprietários no JavaScript

```bash
curl -k --compressed -s https://target/ | grep -oE '"/api/[^"]*"' | sort -u | head -20
```

- `/api/v2/monitor/web-ui/extend-session` → FortiOS
- `/cdn-cgi/` → Cloudflare
- `/_next/static/` → Next.js (pode ser CDN servindo app antigo)
- `/static/admin/` → Django admin (pode ser WAF cacheando)

## ✅ Procedimento Operacional

Quando "URL pública não reflete o que deveria":

1. **Não assumir** que é problema do app ou do código
2. **Coletar evidências materiais** (certificado, headers, body, JS endpoints)
3. **Comparar inside-out** (backend interno → URL pública)
4. **Mapear topologia** de DNS até processo
5. **Isolar a camada problemática** antes de tocar em código

## 🎯 Princípio

> Em ambientes com edge devices, "200 OK" só significa que **alguém**
> respondeu — não necessariamente sua aplicação. Provas materiais
> (cert, body, headers) substituem inferências.

## Mitigações Recomendadas

- [ ] Header `X-Served-By: gunicorn-<hostname>` no app (difícil de falsificar por edge)
- [ ] Endpoint `/api/health` retornar `git_sha` e `hostname` únicos
- [ ] Diagrama de rede em `docs/architecture/network-topology.md`
- [ ] Runbook: "como validar o que está respondendo em prod"

## Referências

- [LL-007](./LL-007-deploy-topology.md) — topologia de deploy
- `docs/infra/ticket-fortigate-publish.md`
- CVEs FortiOS relevantes: CVE-2024-21762, CVE-2024-55591, CVE-2022-40684
