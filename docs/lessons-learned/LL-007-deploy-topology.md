# LL-007 — Mapeamento da topologia completa antes de debugar produção

**Data:** 2026-06-07
**Contexto:** Após build correto da imagem, URL pública ainda servia versão antiga.

## 🔴 Problema

Backend interno (`http://172.29.2.11:5000`) servia código novo,
mas URL pública (`https://noc.grupogadens.com.br/`) servia conteúdo diferente.

## 🧠 Causa Raiz

A topologia real tinha **camadas não mapeadas**:

```
Internet (443)
    ↓
FortiGate WAN [TLS termina AQUI — self-signed CN=FortiGate]
    ↓
[VIP/DNAT não configurado] ← problema
    ↓
172.29.2.11:5000 (gunicorn) ← nunca alcançado
```

A "caixa preta" era a interface admin do próprio FortiGate respondendo
em 443 — VIP/DNAT para o app **nunca foi configurado**.

## ✅ Procedimento de Debug — Inside-Out

Sempre validar do backend para fora, camada por camada:

```bash
# Camada 1: app responde localmente?
curl -s http://localhost:5000/api/health

# Camada 2: app responde na rede interna?
curl -s http://172.29.2.11:5000/api/health

# Camada 3: reverse proxy interno (Nginx/Traefik)?
curl -s http://nginx-host/ -H "Host: noc.grupogadens.com.br"

# Camada 4: edge device (FortiGate)?
curl -k -sI https://noc.grupogadens.com.br/
echo | openssl s_client -connect noc.grupogadens.com.br:443 2>/dev/null \
  | openssl x509 -noout -subject -issuer

# Camada 5: DNS público resolve para onde?
dig +short noc.grupogadens.com.br
```

## 🛡️ Diagnósticos Definitivos por Camada

| Camada | Pergunta | Como responder |
|--------|----------|----------------|
| App | Código novo deployed? | Endpoint `/api/health` com `GIT_SHA` |
| Container | Imagem atual? | `docker compose images app` |
| Reverse proxy | Roteamento ativo? | `nginx -T` ou logs de acesso |
| Edge device | Que cert apresenta? | `openssl s_client -connect ...` |
| DNS | Resolve onde? | `dig`, `nslookup` |

## 🎯 Princípio

> Nunca assuma que `https://dominio/` aponta para sua aplicação.
> Comprove materialmente cada hop. Documente topologia em diagrama vivo no repo.

## Ações Recomendadas

- [ ] Criar `docs/architecture/network-topology.md` com diagrama Mermaid
- [ ] Implementar header `X-Served-By: gunicorn-<hostname>` no app
- [ ] Endpoint `/api/health` retornar `git_sha`, `hostname`, `build_time`

## Referências

- [LL-006](./LL-006-restart-vs-build.md) — restart vs build
- [LL-008](./LL-008-edge-device-masquerade.md) — edge device mascarado
- `docs/infra/ticket-fortigate-publish.md`
