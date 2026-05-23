# 🛡️ Exceções de Segurança — Justificativas Formais

Este documento registra **decisões conscientes** sobre supressões ou
configurações de segurança que se desviam do padrão "deny-by-default".

> Cada exceção deve ter: **risco**, **mitigação**, **escopo**, **owner** e **revisão**.

---

## 📋 Catálogo de Exceções

### EX-001 — TLS Verification Configurável em Scripts de Diagnóstico

| Campo | Valor |
|---|---|
| **ID** | EX-001 |
| **Status** | ✅ Ativa |
| **Owner** | @robertoandr |
| **Criada em** | 2026-05-23 |
| **Revisão** | 2026-11-23 (semestral) |
| **CWE** | [CWE-295](https://cwe.mitre.org/data/definitions/295.html) |
| **Bandit Rule** | B501 |

#### 📝 Contexto

O script `scripts/diag_zbx.py` realiza diagnóstico da API Zabbix em ambientes
internos que frequentemente usam **CA própria** ou certificados auto-assinados.

#### 🎯 Decisão

Em vez de hardcodar `verify=False` (vulnerável a MITM), implementamos:

1. **Padrão seguro**: `verify=True` usa CAs do sistema
2. **Caminho customizado**: `ZBX_CA_BUNDLE=/path/to/ca.pem`
3. **Bypass explícito** (laboratório): `ZBX_VERIFY_TLS=false` + warning visível

#### ⚖️ Trade-offs

| Aspecto | Avaliação |
|---|---|
| **Risco residual** | 🟢 Baixo — opt-in explícito + warning |
| **Usabilidade** | 🟢 Alta — funciona em qualquer ambiente |
| **Auditabilidade** | 🟢 Alta — logs registram a config usada |
| **Compliance** | ✅ NIST SP 800-52 Rev. 2, ISO 27001 A.13.1.1 |

#### 🔁 Critérios de Revisão

A exceção deve ser **reavaliada** se:
- O script for promovido a uso em produção contínua
- Houver incidente envolvendo MITM em rede interna
- O Zabbix for migrado para CA pública (Let's Encrypt, etc.)

#### 📚 Referências

- [Bandit B501](https://bandit.readthedocs.io/en/latest/plugins/b501_request_with_no_cert_validation.html)
- [Requests SSL Verification](https://requests.readthedocs.io/en/latest/user/advanced/#ssl-cert-verification)

---

## 🔄 Histórico de Revisões

| Data | Revisor | Alteração |
|---|---|---|
| 2026-05-23 | @robertoandr | Documento criado com EX-001 |
