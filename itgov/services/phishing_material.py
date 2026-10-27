"""Conteúdo das campanhas de phishing simulado (e-mail, landing, página educativa).

Material versionado aqui para o dashboard criar a campanha no GoPhish sem
ninguém abrir o console. A landing **não guarda a senha digitada**
(``capture_passwords=False``): registra só que a pessoa enviou o formulário.

A primeira isca é "senha do M365 expirando". ``{{.URL}}`` e ``{{.Tracker}}``
são variáveis do próprio GoPhish, preenchidas no envio.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Isca:
    """Conteúdo completo de uma isca de phishing simulado.

    Cada isca vira um template de e-mail + uma landing + uma página educativa
    no GoPhish. ``{{.URL}}`` e ``{{.Tracker}}`` nos HTMLs são variáveis do
    próprio GoPhish, preenchidas no envio.

    Attributes:
        id: Identificador curto e estável (usado na URL da educativa e no form).
        rotulo: Rótulo exibido no seletor da tela.
        template_nome: Nome do template de e-mail no GoPhish (único por isca).
        landing_nome: Nome da landing no GoPhish (único por isca).
        assunto: Assunto do e-mail.
        email_html: HTML do e-mail.
        email_text: Versão texto do e-mail (fallback).
        landing_html: HTML da página falsa.
        educativa_html: HTML mostrado a quem "cai" (explica que era teste).
        captura_credenciais: Se ``False``, a landing não guarda nada do que foi
            digitado — só registra que a pessoa enviou o formulário.
    """

    id: str
    rotulo: str
    template_nome: str
    landing_nome: str
    assunto: str
    email_html: str
    email_text: str
    landing_html: str
    educativa_html: str
    captura_credenciais: bool


ISCA_SENHA_M365 = "senha-m365-expirando"

EMAIL_HTML = '<!doctype html>\n<html lang="pt-BR">\n<head><meta charset="utf-8"></head>\n<body style="margin:0;padding:0;background:#f3f3f3;font-family:Segoe UI,Arial,sans-serif;">\n  <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e3e3e3;">\n    <div style="background:#0067b8;padding:16px 24px;">\n      <span style="color:#ffffff;font-size:18px;font-weight:600;">Microsoft 365</span>\n    </div>\n    <div style="padding:28px 24px;color:#1b1b1b;font-size:15px;line-height:1.55;">\n      <p style="margin:0 0 16px;">Olá,</p>\n      <p style="margin:0 0 16px;">\n        A senha da sua conta corporativa <strong>vai expirar em 24 horas</strong>.\n        Para não perder o acesso ao e-mail e aos arquivos, confirme sua senha atual\n        e mantenha a mesma, se preferir.\n      </p>\n      <p style="margin:0 0 24px;text-align:center;">\n        <a href="{{.URL}}" style="background:#0067b8;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:4px;font-size:15px;display:inline-block;">\n          Manter minha senha\n        </a>\n      </p>\n      <p style="margin:0 0 8px;color:#616161;font-size:13px;">\n        Se o botão não funcionar, copie e cole o endereço no navegador:<br>\n        <span style="color:#0067b8;">{{.URL}}</span>\n      </p>\n      <p style="margin:24px 0 0;color:#616161;font-size:12px;">\n        Mensagem automática — não responda este e-mail.\n      </p>\n    </div>\n  </div>\n  {{.Tracker}}\n</body>\n</html>\n'

LANDING_HTML = '<!doctype html>\n<html lang="pt-BR">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>Entrar</title>\n  <style>\n    body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f2f2f2;}\n    .caixa{max-width:440px;margin:80px auto;background:#fff;padding:44px;box-shadow:0 2px 6px rgba(0,0,0,.1);}\n    .logo{color:#0067b8;font-size:20px;font-weight:600;margin-bottom:24px;}\n    h1{font-size:24px;font-weight:600;margin:0 0 16px;}\n    input{width:100%;box-sizing:border-box;padding:8px 0;border:none;border-bottom:1px solid #666;font-size:15px;margin-bottom:20px;}\n    button{background:#0067b8;color:#fff;border:none;padding:10px 24px;font-size:15px;float:right;cursor:pointer;}\n    .sub{color:#666;font-size:13px;margin:0 0 20px;}\n  </style>\n</head>\n<body>\n  <form class="caixa" method="post">\n    <div class="logo">Microsoft 365</div>\n    <h1>Entrar</h1>\n    <p class="sub">Confirme sua conta corporativa para manter sua senha.</p>\n    <input type="email" name="email" placeholder="E-mail corporativo" autocomplete="off" required>\n    <input type="password" name="password" placeholder="Senha" autocomplete="off" required>\n    <button type="submit">Entrar</button>\n    <div style="clear:both;"></div>\n  </form>\n</body>\n</html>\n'

EDUCATIVA_HTML = '<!doctype html>\n<html lang="pt-BR">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>Simulação de phishing — Governança de TI</title>\n  <style>\n    body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f2f2f2;color:#1b1b1b;}\n    .caixa{max-width:560px;margin:70px auto;background:#fff;padding:44px;border-top:6px solid #c0392b;box-shadow:0 2px 6px rgba(0,0,0,.1);}\n    h1{font-size:24px;margin:0 0 16px;color:#c0392b;}\n    p{font-size:15px;line-height:1.6;margin:0 0 14px;}\n    ul{font-size:15px;line-height:1.7;}\n    .rodape{color:#666;font-size:13px;margin-top:24px;}\n  </style>\n</head>\n<body>\n  <div class="caixa">\n    <h1>Isto era um teste de phishing</h1>\n    <p>Este e-mail foi uma <strong>simulação</strong> da equipe de Governança de TI.\n       Ninguém guardou a sua senha — registramos apenas que o link foi aberto, para medirmos o quanto a empresa está preparada.</p>\n    <p>Como reconhecer um golpe de verdade:</p>\n    <ul>\n      <li>Urgência ("sua senha expira em 24h") para você agir sem pensar.</li>\n      <li>Links que pedem a sua senha fora do portal oficial.</li>\n      <li>Remetente e endereço do link diferentes do habitual.</li>\n    </ul>\n    <p>Na dúvida, <strong>não clique</strong>: encaminhe o e-mail para a TI usando o botão\n       "Reportar phishing" do Outlook.</p>\n    <p class="rodape">Dúvidas? Fale com a equipe de Governança de TI.</p>\n  </div>\n</body>\n</html>\n'

ASSUNTO = "Sua senha corporativa expira em 24 horas"


# ── Isca "RH — prêmio surpresa" ──────────────────────────────────────────────
# E-mail do RH convidando a se inscrever num sorteio e concorrer a um prêmio
# surpresa. Isca de "recompensa boa demais" + urgência (vagas/prazo).

ISCA_RH_PREMIO = "rh-premio-surpresa"

ASSUNTO_RH = "🎁 Inscrições abertas: concorra a um prêmio surpresa (vagas limitadas)"

EMAIL_RH_HTML = '<!doctype html>\n<html lang="pt-BR">\n<head><meta charset="utf-8"></head>\n<body style="margin:0;padding:0;background:#f3f3f3;font-family:Segoe UI,Arial,sans-serif;">\n  <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e3e3e3;">\n    <div style="background:#2e7d32;padding:16px 24px;">\n      <span style="color:#ffffff;font-size:18px;font-weight:600;">Recursos Humanos</span>\n    </div>\n    <div style="padding:28px 24px;color:#1b1b1b;font-size:15px;line-height:1.55;">\n      <p style="margin:0 0 16px;">Olá,</p>\n      <p style="margin:0 0 16px;">\n        O RH preparou uma <strong>campanha de valorização dos colaboradores</strong>!\n        Quem se inscrever concorre a um <strong>prêmio surpresa</strong> que vamos\n        revelar no sorteio desta sexta-feira.\n      </p>\n      <p style="margin:0 0 16px;">\n        As <strong>vagas são limitadas</strong> e a inscrição leva menos de um minuto.\n        Garanta já a sua participação:\n      </p>\n      <p style="margin:0 0 24px;text-align:center;">\n        <a href="{{.URL}}" style="background:#2e7d32;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:4px;font-size:15px;display:inline-block;">\n          Quero participar\n        </a>\n      </p>\n      <p style="margin:0 0 8px;color:#616161;font-size:13px;">\n        Se o botão não funcionar, copie e cole o endereço no navegador:<br>\n        <span style="color:#2e7d32;">{{.URL}}</span>\n      </p>\n      <p style="margin:24px 0 0;color:#616161;font-size:12px;">\n        Mensagem automática do RH — não responda este e-mail.\n      </p>\n    </div>\n  </div>\n  {{.Tracker}}\n</body>\n</html>\n'

EMAIL_RH_TEXT = (
    "O RH está com inscrições abertas para concorrer a um prêmio surpresa. Vagas limitadas! Inscreva-se em {{.URL}}"
)

LANDING_RH_HTML = '<!doctype html>\n<html lang="pt-BR">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>Inscrição — Prêmio surpresa</title>\n  <style>\n    body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f2f2f2;}\n    .caixa{max-width:440px;margin:70px auto;background:#fff;padding:44px;box-shadow:0 2px 6px rgba(0,0,0,.1);}\n    .logo{color:#2e7d32;font-size:20px;font-weight:600;margin-bottom:8px;}\n    h1{font-size:22px;font-weight:600;margin:0 0 8px;}\n    .sub{color:#666;font-size:13px;margin:0 0 24px;}\n    label{display:block;font-size:13px;color:#444;margin:0 0 4px;}\n    input{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid #ccc;border-radius:4px;font-size:15px;margin-bottom:18px;}\n    button{background:#2e7d32;color:#fff;border:none;padding:11px 26px;font-size:15px;border-radius:4px;float:right;cursor:pointer;}\n  </style>\n</head>\n<body>\n  <form class="caixa" method="post">\n    <div class="logo">Recursos Humanos</div>\n    <h1>Inscrição no sorteio</h1>\n    <p class="sub">Preencha para concorrer ao prêmio surpresa. Sorteio nesta sexta.</p>\n    <label for="nome">Nome completo</label>\n    <input id="nome" type="text" name="nome" autocomplete="off" required>\n    <label for="email">E-mail corporativo</label>\n    <input id="email" type="email" name="email" autocomplete="off" required>\n    <button type="submit">Confirmar inscrição</button>\n    <div style="clear:both;"></div>\n  </form>\n</body>\n</html>\n'

EDUCATIVA_RH_HTML = '<!doctype html>\n<html lang="pt-BR">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>Simulação de phishing — Governança de TI</title>\n  <style>\n    body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f2f2f2;color:#1b1b1b;}\n    .caixa{max-width:560px;margin:70px auto;background:#fff;padding:44px;border-top:6px solid #c0392b;box-shadow:0 2px 6px rgba(0,0,0,.1);}\n    h1{font-size:24px;margin:0 0 16px;color:#c0392b;}\n    p{font-size:15px;line-height:1.6;margin:0 0 14px;}\n    ul{font-size:15px;line-height:1.7;}\n    .rodape{color:#666;font-size:13px;margin-top:24px;}\n  </style>\n</head>\n<body>\n  <div class="caixa">\n    <h1>Isto era um teste de phishing</h1>\n    <p>Este e-mail do "RH" foi uma <strong>simulação</strong> da equipe de Governança de TI.\n       Não guardamos nada do que você digitou — registramos apenas que o formulário foi enviado, para medirmos o quanto a empresa está preparada.</p>\n    <p>Como reconhecer um golpe de "prêmio" de verdade:</p>\n    <ul>\n      <li>Recompensa boa demais ("prêmio surpresa", "você foi sorteado").</li>\n      <li>Urgência e escassez ("vagas limitadas", "só até sexta") para você agir sem pensar.</li>\n      <li>Pede seus dados num link em vez dos canais oficiais do RH.</li>\n      <li>Remetente ou endereço do link diferentes do habitual.</li>\n    </ul>\n    <p>Na dúvida, <strong>não clique</strong>: confirme com o RH pelos canais oficiais e encaminhe o e-mail para a TI usando o botão "Reportar phishing" do Outlook.</p>\n    <p class="rodape">Dúvidas? Fale com a equipe de Governança de TI.</p>\n  </div>\n</body>\n</html>\n'


# ── Registro de iscas ────────────────────────────────────────────────────────

ISCAS: dict[str, Isca] = {
    ISCA_SENHA_M365: Isca(
        id=ISCA_SENHA_M365,
        rotulo="Senha do M365 expirando",
        template_nome="Senha M365 expirando",
        landing_nome="Login M365 (simulação)",
        assunto=ASSUNTO,
        email_html=EMAIL_HTML,
        email_text="Sua senha corporativa expira em 24 horas. Acesse {{.URL}} para mantê-la.",
        landing_html=LANDING_HTML,
        educativa_html=EDUCATIVA_HTML,
        captura_credenciais=True,
    ),
    ISCA_RH_PREMIO: Isca(
        id=ISCA_RH_PREMIO,
        rotulo="RH — prêmio surpresa",
        template_nome="RH — prêmio surpresa",
        landing_nome="Inscrição prêmio (simulação)",
        assunto=ASSUNTO_RH,
        email_html=EMAIL_RH_HTML,
        email_text=EMAIL_RH_TEXT,
        landing_html=LANDING_RH_HTML,
        educativa_html=EDUCATIVA_RH_HTML,
        captura_credenciais=False,
    ),
}

ISCA_PADRAO = ISCA_SENHA_M365


def obter(isca_id: str | None) -> Isca | None:
    """Devolve a isca pelo id (ou a padrão se ``isca_id`` for vazio); ``None`` se desconhecida.

    Args:
        isca_id: Id da isca, ou vazio/``None`` para a padrão.

    Returns:
        A :class:`Isca` correspondente, ou ``None`` se o id for inválido.
    """
    return ISCAS.get(isca_id or ISCA_PADRAO)


def opcoes() -> list[dict[str, str]]:
    """Lista ``{"id", "rotulo"}`` das iscas, para o seletor da tela."""
    return [{"id": i.id, "rotulo": i.rotulo} for i in ISCAS.values()]


def educativa_html(isca_id: str | None) -> str:
    """HTML da página educativa da isca (cai na padrão se o id for desconhecido)."""
    isca = obter(isca_id) or ISCAS[ISCA_PADRAO]
    return isca.educativa_html
