"""Títulos em português dos controles do Microsoft Secure Score.

O Graph só devolve título e descrição em inglês (e a descrição vem em HTML).
Aqui ficam título e resumo curtos em PT-BR para os controles avaliados no
tenant; controle novo sem tradução cai no título original da Microsoft.
"""

from __future__ import annotations

import html
import re

# control_name -> (título, o que fazer em uma frase)
CONTROLES_PT: dict[str, tuple[str, str]] = {
    "AdminMFAV2": ("MFA para todos os administradores", "Exigir MFA em todas as contas com função administrativa."),
    "MFARegistrationV2": ("Registro de MFA para todos os usuários", "Garantir que todos os usuários registrem um método de MFA."),
    "BlockLegacyAuthentication": ("Bloquear autenticação legada", "Bloquear protocolos antigos que não suportam MFA (POP, IMAP, SMTP básico)."),
    "SigninRiskPolicy": ("Política de risco de entrada", "Exigir MFA quando o Entra detectar entrada arriscada."),
    "UserRiskPolicy": ("Política de risco de usuário", "Exigir troca de senha quando a conta for considerada comprometida."),
    "SelfServicePasswordReset": ("Redefinição de senha pelo próprio usuário", "Habilitar SSPR para todos os usuários."),
    "PWAgePolicyNew": ("Senhas sem expiração periódica", "Não forçar troca periódica de senha (recomendação atual da Microsoft/NIST)."),
    "PasswordHashSync": ("Sincronização de hash de senha", "Sincronizar hash de senha do AD local para detectar vazamentos."),
    "OneAdmin": ("Mais de um administrador global", "Ter ao menos dois administradores globais (e no máximo quatro)."),
    "RoleOverlap": ("Funções administrativas com menor privilégio", "Dar a cada admin só as funções de que precisa."),
    "IntegratedApps": ("Consentimento de apps pelo usuário restrito", "Não permitir que usuários autorizem apps de terceiros sozinhos."),
    "CustomerLockBoxEnabled": ("Ativar o Customer Lockbox", "Exigir aprovação da empresa antes de a Microsoft acessar os dados em um suporte."),
    "McasFirewallLogUpload": ("Enviar logs do firewall ao Defender for Cloud Apps", "Descobrir apps de nuvem não autorizados (shadow IT) pelos logs do firewall."),
    "dlp_datalossprevention": ("Prevenção de perda de dados (DLP)", "Ter políticas de DLP para dados sensíveis."),
    "exo_individualsharing": ("Limitar compartilhamento de calendário com externos", "Não permitir compartilhar detalhes completos do calendário com pessoas de fora."),
    "exo_mailboxaudit": ("Auditoria das caixas de correio", "Garantir auditoria ativa em todas as caixas."),
    "exo_mailtipsenabled": ("Dicas de e-mail (MailTips)", "Ativar avisos ao enviar para externos ou grupos grandes."),
    "exo_oauth2clientprofileenabled": ("Autenticação moderna no Exchange", "Manter a autenticação moderna (OAuth2) ativada."),
    "exo_outlookaddins": ("Restringir suplementos do Outlook", "Não permitir que usuários instalem suplementos sozinhos."),
    "exo_storageproviderrestricted": ("Restringir armazenamento externo no Outlook Web", "Bloquear Dropbox/Google Drive etc. no Outlook na Web."),
    "exo_transportrulesallowlistdomains": ("Regras de transporte sem domínios liberados", "Não ter regras que liberam domínios inteiros da filtragem."),
    "mdo_allowedsenderscombined": ("Sem remetentes liberados no antispam", "Não manter listas de remetentes/domínios permitidos no antispam."),
    "mdo_antiphishingpolicies": ("Política anti-phishing", "Configurar política anti-phishing do Defender for Office 365."),
    "mdo_atpprotection": ("Defender para SharePoint, OneDrive e Teams", "Ativar Safe Attachments para SharePoint, OneDrive e Teams."),
    "mdo_autoforwardingmode": ("Bloquear encaminhamento automático externo", "Impedir encaminhamento automático de e-mails para fora."),
    "mdo_blockmailforward": ("Bloquear regras de encaminhamento externo", "Bloquear regras de caixa que encaminham e-mail para fora."),
    "mdo_bulkspamaction": ("Ação para e-mail em massa", "Mover e-mails em massa para Lixo Eletrônico."),
    "mdo_bulkthreshold": ("Limite de e-mail em massa", "Usar limite 6 ou menor para classificar e-mail em massa."),
    "mdo_commonattachmentsfilter": ("Filtro de anexos perigosos", "Bloquear tipos de arquivo perigosos (.exe, .js, .vbs…) no antimalware."),
    "mdo_connectionfilter": ("Filtro de conexão sem IPs liberados", "Não manter IPs liberados no filtro de conexão."),
    "mdo_enabledomainstoprotect": ("Proteger domínios da empresa contra imitação", "Incluir os domínios próprios na proteção contra personificação."),
    "mdo_enablemailboxintelligence": ("Inteligência de caixa de correio", "Ativar inteligência de caixa no anti-phishing."),
    "mdo_highconfidencephishaction": ("Ação para phishing de alta confiança", "Colocar phishing de alta confiança em quarentena."),
    "mdo_highconfidencespamaction": ("Ação para spam de alta confiança", "Colocar spam de alta confiança em quarentena."),
    "mdo_mailboxintelligenceprotection": ("Proteção por inteligência de caixa", "Ativar a proteção contra personificação por inteligência de caixa."),
    "mdo_mailboxintelligenceprotectionaction": ("Ação da inteligência de caixa", "Enviar para quarentena o que a inteligência de caixa detectar."),
    "mdo_phishthresholdlevel": ("Nível de sensibilidade a phishing", "Subir o limite de phishing para 2 (agressivo) ou mais."),
    "mdo_phisspamacation": ("Ação para phishing no antispam", "Colocar e-mails de phishing em quarentena."),
    "mdo_quarantineretentionperiod": ("Retenção da quarentena", "Manter mensagens em quarentena por 30 dias."),
    "mdo_recipientexternallimitperhour": ("Limite de destinatários externos por hora", "Limitar envio para externos por hora."),
    "mdo_recipientinternallimitperhour": ("Limite de destinatários internos por hora", "Limitar envio interno por hora."),
    "mdo_recipientlimitperday": ("Limite de destinatários por dia", "Limitar envio diário por usuário."),
    "mdo_safeattachmentpolicy": ("Política de Safe Attachments", "Ter política de anexos seguros aplicada."),
    "mdo_safeattachments": ("Safe Attachments no e-mail", "Verificar anexos em sandbox antes da entrega."),
    "mdo_safedocuments": ("Safe Documents", "Verificar documentos do Office abertos no modo protegido."),
    "mdo_safelinksforOfficeApps": ("Safe Links nos apps do Office", "Verificar links clicados no Word, Excel, PowerPoint e Teams."),
    "mdo_safelinksforemail": ("Safe Links no e-mail", "Verificar links dos e-mails no momento do clique."),
    "mdo_similardomainssafetytips": ("Aviso de domínio parecido", "Mostrar aviso quando o remetente usa domínio parecido com o da empresa."),
    "mdo_similaruserssafetytips": ("Aviso de usuário parecido", "Mostrar aviso quando o remetente imita o nome de um usuário."),
    "mdo_spam_notifications_only_for_admins": ("Notificação de spam só para admins", "Não notificar o usuário sobre mensagens em quarentena por malware."),
    "mdo_spamaction": ("Ação para spam", "Mover spam para Lixo Eletrônico ou quarentena."),
    "mdo_targeteddomainprotectionaction": ("Ação contra personificação de domínio", "Colocar em quarentena e-mail que imita domínios protegidos."),
    "mdo_targeteduserprotectionaction": ("Ação contra personificação de usuário", "Colocar em quarentena e-mail que imita usuários protegidos."),
    "mdo_targetedusersprotection": ("Proteger usuários-chave contra personificação", "Incluir diretoria e financeiro na proteção contra personificação."),
    "mdo_thresholdreachedaction": ("Ação ao atingir limite de envio", "Bloquear o usuário que estourar o limite de envio."),
    "mdo_unusualcharacterssafetytips": ("Aviso de caracteres incomuns", "Mostrar aviso quando o remetente usa caracteres incomuns."),
    "mdo_zapmalware": ("ZAP para malware", "Remover malware de caixas depois da entrega."),
    "mdo_zapphish": ("ZAP para phishing", "Remover phishing de caixas depois da entrega."),
    "mdo_zapspam": ("ZAP para spam", "Remover spam de caixas depois da entrega."),
    "meeting_anonymousstartmeeting_v1": ("Anônimos não iniciam reuniões", "Impedir que anônimos iniciem reuniões do Teams."),
    "meeting_autoadmitusers_v1": ("Sala de espera nas reuniões", "Só admitir automaticamente pessoas da empresa."),
    "meeting_designatedpresenter_v1": ("Apresentadores definidos", "Só quem tem papel de apresentador pode compartilhar conteúdo."),
    "meeting_externalrequestcontrol_v1": ("Externos não pedem controle", "Impedir que externos peçam controle da tela."),
    "meeting_pstnusersbypasslobby_v1": ("Telefone passa pela sala de espera", "Quem entra por telefone aguarda na sala de espera."),
    "meeting_restrictanonymousjoin_v1": ("Restringir anônimos nas reuniões", "Não permitir que anônimos entrem em reuniões do Teams."),
    "mip_autosensitivitylabelspolicies": ("Rotulagem automática", "Aplicar rótulos de sensibilidade automaticamente por conteúdo."),
    "mip_purviewlabelconsent": ("Rótulos no mapa de dados do Purview", "Permitir que rótulos sejam aplicados aos ativos do mapa de dados."),
    "mip_search_auditlog": ("Log de auditoria unificado", "Manter o log de auditoria ativado e pesquisável."),
    "mip_sensitivitylabelspolicies": ("Publicar rótulos de sensibilidade", "Publicar política de rótulos para os usuários classificarem documentos."),
    "spo_idle_session_timeout": ("Encerrar sessão ociosa no SharePoint", "Desconectar sessões ociosas no navegador."),
    "spo_legacy_auth": ("Bloquear autenticação legada no SharePoint", "Bloquear apps antigos no SharePoint e OneDrive."),
}  # fmt: skip


def titulo_pt(control_name: str, original: str = "") -> str:
    """Título em PT-BR; sem tradução, o original (ou o próprio ID)."""
    par = CONTROLES_PT.get(control_name)
    return par[0] if par else (original or control_name)


def resumo_pt(control_name: str, descricao_html: str = "", limite: int = 220) -> str:
    """Resumo em PT-BR; sem tradução, a descrição original em texto puro e cortada."""
    par = CONTROLES_PT.get(control_name)
    if par:
        return par[1]
    texto = html.unescape(re.sub(r"<[^>]+>", " ", descricao_html or ""))
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto if len(texto) <= limite else texto[: limite - 1].rsplit(" ", 1)[0] + "…"
