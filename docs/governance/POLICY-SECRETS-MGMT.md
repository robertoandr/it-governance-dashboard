# Política de Gestão de Secrets

**Documento**: POL-SEC-002
**Versão**: 1.0
**Vigência**: 2026-05-25
**Owner**: Governança de TI

---

## 1. Objetivo

Estabelecer requisitos para gestão do ciclo de vida completo de secrets
(credenciais, API keys, certificados, senhas) em todos os ambientes.

## 2. Escopo

Aplica-se a TODOS os tipos de secrets: API Keys, DB credentials, service
account passwords, TLS keys, SSH keys, JWT keys, encryption keys, webhook
secrets, OAuth client secrets.

## 3. Hierarquia de Armazenamento

1. Secret Manager dedicado (Vault, AWS SM, Azure KV)
2. Variáveis de ambiente em runtime
3. Credential helpers do SO (keychain, libsecret)
4. Arquivos cifrados (sops, ansible-vault) com chave em KMS
5. NUNCA: plaintext em disco, git, wiki, chat

## 4. Requisitos por Categoria

### 4.1 Produção
- Secret Manager OBRIGATÓRIO
- RBAC granular + audit log
- Rotação automática ≤ 90 dias

### 4.2 Desenvolvimento
- Secret Manager OU credential helper de SO
- Dados reais PROIBIDOS em dev
- Rotação ≤ 180 dias

### 4.3 CI/CD
- GitHub Secrets / GitLab Variables / Azure DevOps
- Escopo por repo/pipeline
- Mask obrigatório nos logs
- Preferir OIDC sobre PATs longos

## 5. Controles

### 5.1 Detecção
- Secret scanning nativo (GitHub/GitLab)
- Push protection habilitada
- Gitleaks em pipeline CI
- Scan semanal de filesystem

### 5.2 Prevenção
- Pre-commit hooks (gitleaks/trufflehog)
- Treinamento anual Secure Coding
- Code review com checklist
- .gitignore template corporativo

### 5.3 Resposta
- Runbook de exposição documentado
- Revogação < 1h
- Rotação < 4h
- Post-mortem para secrets de produção

## 6. Tipos Especiais

### 6.1 SSH
- ed25519 ou RSA ≥ 4096
- Passphrase OBRIGATÓRIA
- Uma chave por dispositivo
- Rotação ≤ 365 dias

### 6.2 TLS
- RSA 2048 / ECDSA P-256 mínimo
- Validade ≤ 397 dias
- Automação via ACME
- Alerta T-30d

### 6.3 Service Accounts
- ≥ 32 chars aleatórios
- Nunca reutilizar entre ambientes
- Apenas em Secret Manager

## 7. KPIs

| KPI | Meta |
|-----|------|
| Secrets em git | 0 |
| % serviços com Secret Manager (prod) | 100% |
| % rotação automática | ≥ 80% |
| Tempo médio rotação manual | < 4h |
| Cobertura pre-commit hooks | 100% repos críticos |

## 8. Referências

- ISO/IEC 27001:2022 — A.8.24
- NIST SP 800-57
- OWASP Secrets Management Cheat Sheet
- CIS Controls v8 — Control 3.11, 6.x
