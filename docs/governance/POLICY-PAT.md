# Política de Personal Access Tokens (PAT)

**Documento**: POL-SEC-001
**Versão**: 1.0
**Vigência**: 2026-05-25
**Próxima revisão**: 2027-05-25
**Owner**: Governança de TI
**Aprovado por**: CIO / CISO
**Classificação**: Interno

---

## 1. Objetivo

Estabelecer requisitos mínimos de segurança para criação, uso, armazenamento,
rotação e revogação de Personal Access Tokens (PATs) em todos os sistemas
corporativos.

## 2. Escopo

Aplica-se a todos os colaboradores, terceiros, service accounts e automações,
em todos os ambientes (prod, hml, dev, sandbox).

## 3. Princípios

1. Menor privilégio
2. Expiração obrigatória (máx 90 dias)
3. Rotação periódica
4. Não compartilhamento
5. Armazenamento seguro (nunca plaintext em disco)
6. Auditabilidade
7. Defense in depth (PAT + 2FA obrigatório)

## 4. Requisitos Técnicos

### 4.1 Criação
- OBRIGATÓRIO: fine-grained tokens quando suportado
- OBRIGATÓRIO: expiração ≤ 90 dias
- PROIBIDO: tokens sem expiração
- PROIBIDO: escopo admin sem aprovação CISO

### 4.2 Armazenamento

| Local | Permitido |
|-------|-----------|
| Secret Manager (Vault, AWS SM, Azure KV) | SIM |
| Variável de ambiente em runtime | SIM |
| Credential helper de SO (keychain, libsecret) | SIM |
| gh CLI gerenciado | SIM |
| Arquivo .git-credentials plaintext | NÃO |
| .env commitado | NÃO |
| Dotfiles (.bashrc, .zshrc) | NÃO |
| Documentos / wikis / chats | NÃO |

### 4.3 Rotação
- Periodicidade: ≤ 90 dias
- Notificação: T-7 dias do vencimento
- Procedimento: gerar novo → atualizar consumidores → revogar antigo

### 4.4 Revogação Imediata (< 1h)
- Exposição em plaintext detectada
- Colaborador desligado ou trocou de função
- Suspeita de comprometimento
- Dispositivo perdido/roubado

## 5. Inventário e Auditoria

- Todo PAT ativo deve estar em docs/governance/SECRETS-INVENTORY.md
- Scan semanal de plaintext (gitleaks)
- Inventário diário via API
- Alerta T-7d para vencimento
- Dashboard com KPIs em tempo real

## 6. KPIs

| KPI | Meta | Frequência |
|-----|------|------------|
| PATs em plaintext | 0 | Diária |
| % com expiração ≤ 90d | 100% | Diária |
| % fine-grained | ≥ 90% | Semanal |
| MTTD exposição | < 24h | Por incidente |
| MTTR exposição | < 4h | Por incidente |
| % colaboradores 2FA | 100% | Mensal |

## 7. Sanções

1. Advertência formal (1ª)
2. Treinamento obrigatório (2ª)
3. Suspensão de acesso (3ª)
4. Medidas disciplinares (reincidência)

## 8. Referências

- ISO/IEC 27001:2022 — A.5.15, A.5.16, A.5.17
- NIST SP 800-63B
- CIS Controls v8 — Control 6
- COBIT 2019 — APO13, DSS05

## 9. Histórico

| Versão | Data | Autor | Mudanças |
|--------|------|-------|----------|
| 1.0 | 2026-05-25 | Gov TI | Versão inicial |
