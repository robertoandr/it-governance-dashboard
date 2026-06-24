# Documento de KPIs — Governança Microsoft 365

**Versão:** 1.0
**Data:** 2026-06-23
**Base:** Documento de referência interna "Governança Microsoft 365 — Hub de operação, segurança e licenciamento" (Maio/2026)
**Responsável:** Roberto Andrade
**Status:** Documentação antes da execução — NÃO IMPLEMENTAR sem revisão deste doc

---

## 1. Mapa de implementação atual vs documento de referência

### 1.1 O que já está implementado (Sprint 12)

| KPI | Pilar | Endpoint Graph | Status |
|-----|-------|---------------|--------|
| % MFA habilitado | Identidade | `/reports/authenticationMethods/userRegistrationDetails` | Implementado |
| Total de usuários | Identidade | `/users` | Implementado |
| Contas inativas 90d+ | Identidade | `/users` (signInActivity) | Implementado (collector) |
| Licenças ativas por plano | Licenciamento | `/subscribedSkus` | Implementado |
| Dispositivos registrados no Entra ID | Dispositivos | `/devices` | Implementado |
| Dispositivos inativos 90d+ | Dispositivos | `/devices` (approximateLastSignInDateTime) | Implementado |
| App registrations ativas | Aplicativos | `/applications` | Implementado |
| Certificados expirados/a vencer 30d | Aplicativos | `/applications` (keyCredentials) | Implementado |
| Secure Score geral (%) | Compliance | `/security/secureScores` | Implementado |
| Secure Score por categoria (Identity/Apps/Data) | Compliance | `/security/secureScores` | Implementado |
| Sensitivity Labels ativas | Dados | `/security/dataSecurityAndGovernance/sensitivityLabels` | Implementado |
| Status dos serviços M365 | Service Health | `/admin/serviceAnnouncement/issues` | Implementado |

### 1.2 Gaps identificados — KPIs do documento não implementados

Os itens abaixo constam na seção 7.2 (Indicadores de saúde) e nos 6 pilares (seção 4) do documento mas **não existem no dashboard**.

---

## 2. KPIs novos — especificação por pilar

### 2.1 Pilar Identidade (Entra ID P1)

#### KPI-ID-01 — Acesso Condicional: políticas ativas
- **O que mede:** número de políticas de Acesso Condicional habilitadas vs total cadastradas
- **Meta do documento:** políticas devem cobrir bloqueio por geolocalização e dispositivo não confiável
- **Endpoint Graph:** `GET /identity/conditionalAccess/policies`
- **Permissão necessária:** `Policy.Read.All` (Application)
- **Campos relevantes:** `state` (enabled/disabled/enabledForReportingButNotEnforced), `conditions`, `grantControls`
- **Saída esperada:** `{ total: N, enabled: N, report_only: N, disabled: N }`
- **Alerta:** < 3 políticas enabled → ATENÇÃO

#### KPI-ID-02 — SSPR (Self-Service Password Reset): % usuários registrados
- **O que mede:** percentual de usuários que completaram registro no SSPR
- **Meta do documento:** reduzir chamados ao N1 de reset de senha
- **Endpoint Graph:** `GET /reports/authenticationMethods/userRegistrationDetails`
  (mesmo endpoint já usado para MFA — campo `isSsprRegistered`)
- **Permissão necessária:** `UserAuthenticationMethod.Read.All` — **já temos**
- **Campos relevantes:** `isSsprRegistered`, `isSsprCapable`
- **Saída esperada:** `{ sspr_registered_pct: 0-100 }`
- **Alerta:** < 70% → ATENÇÃO; < 50% → CRITICO
- **Observação:** campo já disponível na chamada existente — custo zero de nova permissão

#### KPI-ID-03 — Admins sem MFA
- **O que mede:** número de contas com papel de administrador global/privilegiado sem MFA ativo
- **Meta do documento:** zero admins sem MFA (implícito em "MFA obrigatório sem exceção")
- **Endpoint Graph:** combinar `GET /directoryRoles/members` + registro MFA existente
- **Permissão necessária:** `RoleManagement.Read.Directory` (Application) — **nova permissão**
- **Saída esperada:** `{ admin_total: N, admin_sem_mfa: N }` — meta: admin_sem_mfa = 0
- **Alerta:** qualquer admin_sem_mfa > 0 → CRITICO

---

### 2.2 Pilar Endpoint — Microsoft Defender for Business

> **Nota de tenant:** Defender for Business é licenciado via Business Premium mas requer onboarding dos endpoints no portal `security.microsoft.com`. A disponibilidade dos dados via Graph depende de o tenant ter completado esse onboarding.

#### KPI-END-01 — Alertas de segurança abertos > 24h
- **O que mede:** número de alertas de severity High/Medium sem resolução há mais de 24h
- **Meta do documento:** "Zero alertas críticos abertos por mais de 24h" (seção 7.2)
- **Endpoint Graph:** `GET /security/alerts_v2?$filter=status eq 'new' and severity in ('high','medium')`
- **Permissão necessária:** `SecurityAlert.Read.All` (Application) — **nova permissão**
- **Campos relevantes:** `severity`, `status`, `createdDateTime`
- **Saída esperada:** `{ alertas_criticos_abertos: N, alertas_24h: N }`
- **Alerta:** alertas_24h > 0 → CRITICO

#### KPI-END-02 — Endpoints com Defender ativo
- **O que mede:** total de endpoints onboarded no Defender for Business vs total de dispositivos
- **Endpoint Graph:** `GET /security/microsoft.graph.security.runHuntingQuery` (Advanced Hunting) ou `GET /deviceManagement/managedDevices` (Intune — **não disponível neste tenant**)
- **Alternativa sem Intune:** Secure Score control `Microsoft Defender for Endpoint` já reflete o onboarding indiretamente
- **Decisão:** usar o controle `secureScoreControlProfiles` filtrado por `controlCategory eq 'Device'` como proxy até o tenant ter Intune/Defender onboarding completo
- **Status:** **BLOQUEADO** — requer confirmação se Defender for Business está onboarded

---

### 2.3 Pilar Dispositivos (Entra ID / sem Intune)

> **Confirmado:** este tenant não usa Microsoft Intune. Dispositivos são gerenciados via `/devices` do Entra ID. KPIs que dependem exclusivamente do Intune são marcados como BLOQUEADOS.

#### KPI-DISP-01 — % dispositivos compliant (sem Intune)
- **O que mede:** proporção de dispositivos com `isCompliant = true` no Entra ID
- **Nota:** `isCompliant` no Entra ID é preenchido pelo Intune. Sem Intune, esse campo fica null/false para todos. **Não implementar** — métrica sem dado real disponível
- **Alternativa útil:** `trustType` (AzureAD vs Hybrid) e `managementType` como indicadores de higiene
- **Status:** **BLOQUEADO sem Intune**

#### KPI-DISP-02 — Dispositivos híbridos vs cloud-only
- **O que mede:** proporção de dispositivos em Hybrid Azure AD Join vs Azure AD Join puro
- **Endpoint Graph:** `/devices` campo `trustType` — **já coletado**
- **Ação:** adicionar breakdown `{ cloud_only: N, hybrid: N, personal: N }` no pilar Dispositivos
- **Status:** **viável com dados atuais**

---

### 2.4 Pilar E-mail — Defender for Office 365 P1

#### KPI-EMAIL-01 — Safe Links / Safe Attachments habilitados
- **O que mede:** se as políticas de Safe Links e Safe Attachments estão ativas no tenant
- **Endpoint Graph:** **não disponível via Graph** — requer Exchange Online PowerShell (`Get-SafeLinksPolicy` / `Get-SafeAttachmentPolicy`)
- **Alternativa:** o Secure Score já pontua a ausência dessas políticas nos controles `EnableSafeLinksForEmail` e `EnableSafeAttachmentForEmail`
- **Ação recomendada:** extrair esses dois controles específicos do Secure Score ao invés de chamar uma API separada
- **Endpoint:** `GET /security/secureScoreControlProfiles?$filter=controlName in ('EnableSafeLinksForEmail','EnableSafeAttachmentForEmail')` — **já temos a permissão**
- **Status:** **viável via Secure Score controls**

#### KPI-EMAIL-02 — DMARC/SPF/DKIM configurados
- **O que mede:** se os registros de autenticação de e-mail estão publicados no DNS
- **Endpoint Graph:** não disponível via Graph
- **Alternativa:** verificação DNS pública via `checkdmarc` ou `dnspython` — leitura do domínio em `.env`
- **Status:** **viável via DNS lookup** (sem Graph, sem nova permissão)

---

### 2.5 Pilar Dados — Microsoft Purview / DLP

#### KPI-DADOS-01 — DLP Policies ativas
- **O que mede:** número de políticas DLP configuradas e ativas no Purview
- **Endpoint Graph:** **não disponível via Microsoft Graph** (confirmado em sessão anterior — gerenciado só via Purview Compliance Portal / PowerShell)
- **Alternativa:** manter `total_labels = 0` como achado de governança (gap real de DLP, não erro)
- **Status:** **BLOQUEADO** — documentar como gap de configuração, não bug

#### KPI-DADOS-02 — Classificação automática: documentos com label aplicada
- **O que mede:** volume de arquivos no SharePoint/OneDrive com sensitivity label aplicada
- **Endpoint Graph (beta):** `GET /beta/reports/security/microsoft.graph.security.getLabelsReport`
- **Permissão:** `Reports.Read.All` — **nova permissão**
- **Status:** **viável, beta — baixa prioridade** (só faz sentido quando tenant tiver labels aplicadas)

---

### 2.6 Pilar Auditoria e Retenção

#### KPI-AUD-01 — Log de auditoria unificado: ativo/inativo
- **O que mede:** se o audit log do Microsoft 365 está habilitado no tenant
- **Endpoint Graph:** não disponível via Graph; depende de Exchange Online PowerShell (`Get-AdminAuditLogConfig`) ou Compliance API
- **Alternativa:** Secure Score control `TurnOnAuditDataRecording` — mesmo padrão do Safe Links
- **Status:** **viável via Secure Score controls**

#### KPI-AUD-02 — Políticas de retenção ativas
- **Endpoint Graph:** não disponível via Graph — Purview API
- **Status:** **BLOQUEADO**

---

## 3. KPIs de licenciamento e custos

### KPI-LIC-01 — Alerta de reajuste julho/2026

> Reajuste Microsoft programado para 01/07/2026:
> - Business Basic: US$ 6,00 → US$ 7,00 (+16,7%)
> - Business Standard: US$ 12,50 → US$ 14,00 (+12%)
> - Business Premium: mantido em US$ 22,00

- **O que mede:** countdown para 01/07/2026 com alerta de renovação antecipada
- **Fonte:** estático (datas conhecidas), não requer Graph
- **Status no dashboard:** banner de countdown já implementado em `m365_overview.html` (commit `2c2e2c7`)
- **Ação:** verificar se o banner expira automaticamente após 01/07/2026 ou precisa de lógica de remoção

### KPI-LIC-02 — Licenças não atribuídas (ociosidade)
- **O que mede:** licenças pagas mas sem usuário atribuído por plano
- **Fórmula:** `prepaidUnits.enabled - consumedUnits` (por SKU)
- **Endpoint Graph:** `/subscribedSkus` — **já implementado**
- **Ação:** adicionar campo `unassigned` no modelo `M365LicenseInfo` e exibir no painel de licenças
- **Status:** **viável com dados atuais** — apenas cálculo e display novos

---

## 4. Indicadores de saúde do tenant — alinhamento com seção 7.2

| Indicador (doc seção 7.2) | KPI mapeado | Implementado? | Prioridade |
|--------------------------|-------------|---------------|------------|
| Secure Score >= 75% (meta >85% até dez/2026) | governance_compliance | Sim | — |
| 100% usuários com MFA | governance_mfa (mfa_enabled_pct) | Sim | — |
| 100% endpoints corporativos no Intune | KPI-DISP-01 | Bloqueado (sem Intune) | — |
| Zero alertas críticos abertos > 24h | KPI-END-01 | Não | Alta |
| MTTR incidente segurança < 1h | Não mapeável via Graph | N/A | — |

---

## 5. Priorização de implementação

### Alta prioridade (viável, nova permissão simples ou sem permissão nova)

| # | KPI | Nova permissão | Esforço |
|---|-----|---------------|---------|
| 1 | KPI-ID-02 — SSPR % | Nenhuma (já temos) | Baixo — campo no payload existente |
| 2 | KPI-LIC-02 — Licenças ociosas | Nenhuma (já temos) | Baixo — cálculo + display |
| 3 | KPI-ID-01 — Acesso Condicional | `Policy.Read.All` | Médio |
| 4 | KPI-EMAIL-01 / KPI-AUD-01 via Secure Score controls | Nenhuma (já temos secureScores) | Baixo — filtrar controles específicos |

### Média prioridade (nova permissão, dependência de confirmação do tenant)

| # | KPI | Nova permissão | Pré-requisito |
|---|-----|---------------|---------------|
| 5 | KPI-END-01 — Alertas Defender > 24h | `SecurityAlert.Read.All` | Confirmar se Defender for Business está onboarded |
| 6 | KPI-ID-03 — Admins sem MFA | `RoleManagement.Read.Directory` | — |
| 7 | KPI-EMAIL-02 — DMARC/SPF/DKIM | Nenhuma (DNS público) | Script Python `dnspython` |

### Baixa prioridade / bloqueado

| # | KPI | Motivo |
|---|-----|--------|
| KPI-DISP-01 | Dispositivos compliant | Requer Intune — não usado |
| KPI-DADOS-01 | DLP Policies | Não disponível via Graph |
| KPI-AUD-02 | Retenção | Não disponível via Graph |
| KPI-END-02 | Endpoints Defender (contagem) | Requer Intune ou Defender onboarding confirmado |

---

## 6. Novas permissões Graph a solicitar

Se aprovado implementar todos os KPIs de alta/média prioridade, adicionar ao App Registration no Entra ID:

| Permissão | Tipo | KPI | Justificativa |
|-----------|------|-----|---------------|
| `Policy.Read.All` | Application | KPI-ID-01 | Leitura de políticas de Acesso Condicional |
| `SecurityAlert.Read.All` | Application | KPI-END-01 | Alertas do Defender for Business |
| `RoleManagement.Read.Directory` | Application | KPI-ID-03 | Membros de roles de administrador |

Permissões **sem alteração necessária** (já existentes):
- `UserAuthenticationMethod.Read.All` — cobre KPI-ID-02 (SSPR)
- `SecurityEvents.Read.All` ou escopo existente de secureScores — cobre controles Secure Score

---

## 7. Checklist de validação antes de implementar

- [ ] Roberto confirma se Defender for Business está onboarded (afeta KPI-END-01 e KPI-END-02)
- [ ] Solicitar consentimento de admin para `Policy.Read.All` e `SecurityAlert.Read.All` no Entra ID
- [ ] Verificar se `RoleManagement.Read.Directory` exige aprovação adicional do tenant
- [ ] Confirmar domínio de e-mail do tenant para implementar KPI-EMAIL-02 (DMARC check)
- [ ] Definir sprint de execução: Sprint 13 ou hotfix no feat/shell-v12?
