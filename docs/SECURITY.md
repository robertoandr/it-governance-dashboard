# Politica de Seguranca - IT Governance Dashboard

## Politica de Logging

### Proibido logar
- Senhas, tokens, API keys, secrets
- Conteudo bruto de objetos config que possam conter credenciais
- Excecoes completas sem sanitizacao

### Como logar com seguranca
- Use app_utils.safe() para sanitizar entradas de usuario antes do log
- Use log.exception(contexto) sem incluir a exception
- Prefira logging estruturado com extra ao inves de interpolacao manual

### Exemplo de uso

Vulneravel a log-injection (CWE-117):
    log.warning("ack %s: %s", eventid, e)

Seguro:
    from app_utils import safe
    log.warning("ack %s: %s", safe(eventid), safe(e))

## Riscos Aceitos Documentados

### scripts/diag_zbx.py - verify=False (alerts #11, #12)

Severidade: HIGH (py/request-without-cert-validation)
Decisao: Wont fix - risco aceito

Justificativa:
- Script de diagnostico standalone, executado manualmente
- Conecta a Zabbix interno via .env local
- Ambiente corporativo com certificado auto-assinado em rede interna
- Nao e importado pela aplicacao web (verificado via grep)
- Risco residual: MITM na rede interna (mitigado por controles de rede)

Revisao: anual ou ao migrar Zabbix para CA corporativa.

## Triagem de Alertas CodeQL

1. Todo PR com alerta novo requer revisao
2. Decisao: Fix, Dismiss (justificado aqui), ou Track (issue)
3. Revisao trimestral dos dismisses ativos

## Reportando Vulnerabilidades

Canal privado: GitHub Security Advisories.
