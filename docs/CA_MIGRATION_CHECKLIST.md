# Migração: Security Defaults → Conditional Access (grupogadens.com.br)

**Objetivo**: substituir os Security Defaults por políticas de Conditional
Access equivalentes, sem nenhum momento sem proteção de MFA.

**Script**: `infra-setup/06_create_ca_policies.py`
**Status em 2026-07-10**: script criado e validado em dry-run contra o tenant
real. Nada foi criado/alterado ainda — aguardando revisão e execução manual.

## Estado do tenant confirmado em 2026-07-10 (dry-run)

- Total usuários: 372 · MFA registrado: 85.39% (298/349) · SSPR: 76.34%
- Admins sem MFA: 0 (7 admins, todos com MFA) — bom ponto de partida
- Guest users: 1
- **Conta de emergência**: o UPN `flavio2022@grupogadens.onmicrosoft.com`
  citado inicialmente **não existe** no tenant (404 no Graph). O único
  usuário `flavio*` real é `flavio@grupogadens.com.br` (Flávio Mendonça de
  Araújo) — o script já usa esse como default, mas **confirme antes do
  Passo 1** que esta é de fato a conta que deve ficar de fora do MFA
  obrigatório.
- **Permissão do App Registration**: `governanca-ti-m365` tem hoje só
  `Policy.Read.All` (leitura). **Falta** `Policy.ReadWrite.ConditionalAccess`
  — necessário antes de rodar o script com `--apply` (ver Passo 0).
- **As 2 políticas do SharePoint admin center** (`Block access from apps on
  unmanaged devices` e `Use app-enforced Restrictions for browser access`)
  estavam `disabled` até mais cedo hoje e **já foram alteradas para
  `enabledForReportingButNotEnforced`** (timestamps de modificação
  ~15:00 UTC de 2026-07-10) — o Passo 5 abaixo já está feito, não precisa
  repetir.

## Passo 0 — Conceder permissão de escrita (pré-requisito)

No Portal Entra ID:

```
App registrations → governanca-ti-m365 → API permissions →
Add a permission → Microsoft Graph → Application permissions →
Policy.ReadWrite.ConditionalAccess → Add permissions →
Grant admin consent for grupogadens
```

Sem isso, `06_create_ca_policies.py --apply` aborta antes de escrever nada
(o script checa a permissão no token e recusa `--apply` sem ela).

## Passo 1 — Criar as 3 políticas em report-only

```bash
cd /home/zabbix/projects/it-governance-dashboard
python3 infra-setup/06_create_ca_policies.py            # dry-run primeiro — confira a saída
python3 infra-setup/06_create_ca_policies.py --apply     # cria de verdade
```

Cria (idempotente — pode rodar de novo sem duplicar):
- `GG - Require MFA for All Users` (exclui a conta de emergência)
- `GG - Block Legacy Authentication`
- `GG - Require MFA for Admins`

Todas nascem em `enabledForReportingButNotEnforced` — **nada é bloqueado
ainda**. Elas convivem com os Security Defaults, que continuam ativos.

## Passo 2 — Aguardar 7 dias em report-only

No Portal Entra ID: **Conditional Access → Insights and reporting**, filtrar
pelas 3 políticas `GG - *`. Verificar:
- Nenhum usuário legítimo sendo pego por engano nas condições de "Block
  Legacy Authentication" (falso positivo bloquearia e-mail/apps antigos)
- A cobertura de MFA report-only bate com o esperado (~349 usuários com MFA
  registrado devem passar; os ~23 sem MFA vão aparecer como "would block"
  quando a política 1 for promovida)
- Nenhum admin caindo fora da política 3 por mudança de role no meio do
  caminho

## Passo 3 — Desabilitar Security Defaults

Portal Entra ID → **Properties** → **Manage security defaults** →
**Disabled** → salvar. Confirmar que as 3 políticas `GG - *` já estão
criadas e revisadas (Passo 2) **antes** deste passo — é o único momento em
que o tenant fica sem nenhuma das duas proteções ativas até o Passo 4.

## Passo 4 — Promover as 3 políticas GG para Enabled

Via portal (Conditional Access → selecionar a política → State → On) ou
reexecutando o script após alterar o `state` nas definições em
`06_create_ca_policies.py` de `enabledForReportingButNotEnforced` para
`enabled` e rodando `--apply` de novo (o `ensure_policy()` faz PATCH, não
recria).

Ordem recomendada: **Block Legacy Authentication** primeiro (baixo risco de
travar usuário legítimo), depois **Require MFA for Admins**, por último
**Require MFA for All Users** (maior impacto, mais gente pode ser pega sem
MFA registrado).

## Passo 5 — ~~Habilitar as 2 políticas do SharePoint admin center~~ (já feito)

Já estavam em `enabledForReportingButNotEnforced` a partir de 2026-07-10
(ver "Estado do tenant" acima). Se ainda não foram promovidas para
`enabled`, avaliar isso junto com o Passo 4.

## Passo 6 — Monitorar sign-in logs por 48h

Portal Entra ID → **Sign-in logs** → filtrar por **Conditional Access** →
status **Failure**. Procurar por:
- Falhas em massa de um mesmo app/client (possível bloqueio indevido de
  Legacy Auth ainda em uso por algum serviço)
- Usuários específicos travados repetidamente sem conseguir completar MFA
- A conta de emergência (`flavio@grupogadens.com.br`, a confirmar) nunca
  deve aparecer bloqueada — se aparecer, revisar a exclusão na política 1

## Rollback rápido

Qualquer política `GG - *` pode voltar para `state: disabled` via portal ou
via `06_create_ca_policies.py --apply` após editar o `state` na definição —
`ensure_policy()` atualiza (PATCH) a política existente pelo nome, não cria
duplicata. Sem exclusão automática de políticas — remoção é manual pelo
portal, por segurança.
