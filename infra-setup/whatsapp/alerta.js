// Webhook "WhatsApp (Evolution)": um alerta por mensagem, em português simples.
// Fora da janela (padrão 08:00–19:00) não envia nada: o alerta fica registrado
// no Zabbix e entra no resumo da manhã (resumo.js).

function dentroDaJanela(agoraMs, p) {
    var l = local(agoraMs, parseFloat(p.fuso_horas));
    var agora = l.hora * 60 + l.minuto;
    var ini = minutos(p.janela_inicio), fim = minutos(p.janela_fim);
    return ini <= fim ? agora >= ini && agora < fim : agora >= ini || agora < fim;
}

function mensagemAlerta(p) {
    var host = texto(p.host) || 'Equipamento';
    var prob = traduzir(texto(p.event_name));
    var linhas;

    if (texto(p.update_status) === '1') {
        linhas = ['📝 *' + host + '* — ' + prob.curto];
        var quem = texto(p.update_user);
        linhas.push((quem ? quem + ' ' : 'Alguém ') + (texto(p.update_action) || 'atualizou o alerta') + '.');
        if (texto(p.update_message)) {
            linhas.push('"' + texto(p.update_message) + '"');
        }
        return linhas.join('\n');
    }

    if (texto(p.event_value) === '0') {
        linhas = ['✅ *' + host + '* voltou ao normal'];
        var inicio = hhmm(p.start_time), fim = hhmm(p.recovery_time), tempo = duracao(texto(p.duration));
        var trecho = 'Estava com ' + prob.curto;
        if (inicio && fim) {
            trecho += ' das ' + inicio + ' às ' + fim;
        }
        linhas.push(trecho + (tempo ? ' (' + tempo + ').' : '.'));
        return linhas.join('\n');
    }

    var sev = severidade(p.severity);
    linhas = [sev.emoji + ' *' + host + '* ' + prob.frase];
    var desde = hhmm(p.start_time);
    linhas.push((desde ? 'Desde ' + desde + ' · ' : '') + 'prioridade ' + sev.nome);
    var medida = /(\d+(?:[.,]\d+)?)\s*(ms|%)/.exec(texto(p.opdata));
    if (medida) {
        linhas.push(medida[2] === '%' ? 'Perda de ' + medida[1] + '%' : 'Tempo de resposta: ' + medida[1] + ' ms');
    }
    linhas.push('Chamado aberto no Zendesk.');
    if (texto(p.link)) {
        linhas.push('Detalhes: ' + texto(p.link));
    }
    return linhas.join('\n');
}

function principal(p, agoraMs) {
    if (!texto(p.to)) {
        throw 'destino vazio (preencha "Enviar para" com o JID do grupo)';
    }
    if (!dentroDaJanela(agoraMs, p)) {
        return 'Fora do horário (' + p.janela_inicio + '–' + p.janela_fim + '): vai no resumo da manhã';
    }
    enviarWhatsApp(p, mensagemAlerta(p));
    return 'OK';
}

if (typeof value === 'string') {
    try {
        var params = JSON.parse(value);
        return principal(params, params.agora_ms ? parseInt(params.agora_ms, 10) : Date.now());
    } catch (error) {
        Zabbix.log(3, '[WhatsApp webhook] ' + error);
        throw 'WhatsApp webhook falhou: ' + error;
    }
}
