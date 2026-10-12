// Item Script "Resumo da manhã no WhatsApp": roda às 08:00, 14:00 e 18:00 e
// manda, num bloco só, o que o webhook segurou desde o último marco: em dia
// útil, a noite (19:00 → 08:00); em fim de semana/feriado, cada intervalo
// entre os resumos (18:00 → 08:00 → 14:00 → 18:00).
// A fila é o próprio histórico de alertas do Zabbix (alert.get), sem estado.

var LIMITE_LINHAS = 25;

function chamarApi(p, metodo, args) {
    var req = new HttpRequest();
    req.addHeader('Content-Type: application/json-rpc');
    req.addHeader('Authorization: Bearer ' + p.api_token);
    var resp = req.post(p.api_url, JSON.stringify({jsonrpc: '2.0', method: metodo, params: args, id: 1}));
    if (req.getStatus() >= 300) {
        throw 'API do Zabbix respondeu HTTP ' + req.getStatus() + ' em ' + metodo;
    }
    var json = JSON.parse(resp);
    if (json.error) {
        throw 'API do Zabbix (' + metodo + '): ' + (json.error.data || json.error.message);
    }
    return json.result;
}

var TOLERANCIA_MS = 30 * 60000; // atraso aceito entre o horário agendado e a execução

/** Marcos de um dia (ms UTC): resumos e fim da janela ao vivo dos dias úteis. */
function marcosDoDia(meiaNoite, p) {
    var l = local(meiaNoite + 12 * 3600000, parseFloat(p.fuso_horas));
    var marcos = [];
    if (ehFolga(l, p.feriados_extras)) {
        horarios(p.resumos_folga).forEach(function (h) {
            marcos.push({ms: meiaNoite + minutos(h) * 60000, resumo: true});
        });
    } else {
        marcos.push({ms: meiaNoite + minutos(p.janela_inicio) * 60000, resumo: true});
        marcos.push({ms: meiaNoite + minutos(p.janela_fim) * 60000, resumo: false});
    }
    return marcos;
}

/**
 * Período que o resumo de agora cobre: do último marco (resumo anterior ou
 * fim da janela ao vivo) até o último horário de resumo já alcançado.
 * Em dia útil o item também roda às 14:00 e 18:00 (horários de folga); aí
 * não há resumo e a função devolve null.
 */
function periodoDoResumo(agoraMs, p) {
    var fuso = parseFloat(p.fuso_horas);
    var l = local(agoraMs, fuso);
    var hoje = Date.UTC(l.ano, l.mes, l.dia) - fuso * 3600000;
    var marcos = [];
    for (var d = 0; d <= 10; d++) {
        marcos = marcos.concat(marcosDoDia(hoje - d * 86400000, p));
    }
    marcos.sort(function (a, b) { return b.ms - a.ms; });

    var deHoje = marcosDoDia(hoje, p);
    var agendados = horarios(p.resumos_folga).concat([p.janela_inicio]);
    for (var i = 0; i < agendados.length; i++) {
        var slot = hoje + minutos(agendados[i]) * 60000;
        var ehResumo = deHoje.some(function (m) { return m.resumo && m.ms === slot; });
        if (!ehResumo && agoraMs >= slot && agoraMs < slot + TOLERANCIA_MS) {
            return null;
        }
    }

    var fim = null;
    for (var k = 0; k < marcos.length; k++) {
        if (fim === null && marcos[k].resumo && marcos[k].ms <= agoraMs) {
            fim = marcos[k].ms;
        } else if (fim !== null && marcos[k].ms < fim) {
            return {inicio: marcos[k].ms, fim: fim, folga: ehFolga(local(fim, fuso), p.feriados_extras)};
        }
    }
    throw 'sem horário de resumo nos últimos 10 dias';
}

function horaDe(segundos, p) {
    var l = local(parseInt(segundos, 10) * 1000, parseFloat(p.fuso_horas));
    return dois(l.hora) + ':' + dois(l.minuto);
}

/**
 * Agrupa os problemas por host + tipo: o mesmo canal que caiu 5 vezes vira
 * uma linha só. O estado do grupo é o da ocorrência mais recente.
 */
function agrupar(eventos, recuperacoes) {
    var grupos = {}, ordem = [];
    eventos.sort(function (a, b) { return parseInt(a.clock, 10) - parseInt(b.clock, 10); });
    for (var i = 0; i < eventos.length; i++) {
        var e = eventos[i];
        var host = e.hosts && e.hosts.length ? e.hosts[0].name : 'Equipamento';
        var curto = traduzir(e.name).curto;
        var chave = host + '|' + curto;
        if (!grupos[chave]) {
            grupos[chave] = {host: host, curto: curto, vezes: 0};
            ordem.push(chave);
        }
        var g = grupos[chave];
        g.vezes++;
        g.inicio = e.clock;
        g.fim = e.r_eventid !== '0' ? recuperacoes[e.r_eventid] : undefined;
        g.ativo = e.r_eventid === '0';
    }
    return ordem.map(function (k) { return grupos[k]; });
}

function linhaGrupo(g, p) {
    var vezes = g.vezes > 1 ? ' ' + g.vezes + ' vezes' : '';
    if (g.ativo) {
        return '• ' + g.host + ' — ' + g.curto + vezes + ' (desde ' + horaDe(g.inicio, p) + ')';
    }
    var quando = g.fim ? horaDe(g.inicio, p) + ' às ' + horaDe(g.fim, p) : horaDe(g.inicio, p);
    return '• ' + g.host + ' — ' + g.curto + vezes + (g.vezes > 1 ? ' (última: ' : ' (') + quando + ')';
}

function secao(titulo, grupos, p) {
    if (!grupos.length) {
        return [];
    }
    var linhas = ['', titulo + ' (' + grupos.length + ')'];
    for (var i = 0; i < grupos.length && i < LIMITE_LINHAS; i++) {
        linhas.push(linhaGrupo(grupos[i], p));
    }
    if (grupos.length > LIMITE_LINHAS) {
        linhas.push('… e mais ' + (grupos.length - LIMITE_LINHAS) + ' (veja no Zabbix)');
    }
    return linhas;
}

/** "08:00, 14:00 e 18:00". */
function emLista(itens) {
    return itens.length > 1 ? itens.slice(0, -1).join(', ') + ' e ' + itens[itens.length - 1] : itens.join('');
}

function mensagemResumo(grupos, periodo, p) {
    var horaFim = local(periodo.fim, parseFloat(p.fuso_horas)).hora;
    var saudacao = horaFim < 12 ? '☀️ *Bom dia!' : horaFim < 18 ? '🕑 *Boa tarde!' : '🌆 *Boa noite!';
    var parte = horaFim <= 12 ? 'noite' : horaFim <= 15 ? 'manhã' : 'tarde';
    var intervalo = '(' + horaDe(periodo.inicio / 1000, p) + ' às ' + horaDe(periodo.fim / 1000, p) + ')';
    if (!grupos.length) {
        return saudacao + '* Nenhum alerta durante a ' + parte + ' ' + intervalo + '. ✅';
    }
    var ativos = grupos.filter(function (g) { return g.ativo; });
    var resolvidos = grupos.filter(function (g) { return !g.ativo; });
    var linhas = [saudacao + ' Resumo dos alertas da ' + parte + '* ' + intervalo];
    if (periodo.folga) {
        linhas.push('📅 Fim de semana/feriado: alertas só às ' + emLista(horarios(p.resumos_folga)) + '.');
    }
    linhas = linhas.concat(secao('🔴 *Ainda com problema*', ativos, p));
    linhas = linhas.concat(secao('✅ *Já voltaram ao normal*', resolvidos, p));
    linhas.push('', 'Os chamados no Zendesk foram abertos normalmente durante a ' + parte + '.');
    return linhas.join('\n');
}

function principal(p, agoraMs) {
    var janela = periodoDoResumo(agoraMs, p);
    if (janela === null) {
        return 'Dia útil: sem resumo agora (os alertas vão na hora)';
    }
    var alertas = chamarApi(p, 'alert.get', {
        output: ['eventid', 'p_eventid'],
        actionids: [p.acao_id],
        mediatypeids: [p.mediatype_id],
        time_from: Math.floor(janela.inicio / 1000),
        time_till: Math.floor(janela.fim / 1000) - 1
    });

    var problemas = {}, ids = [];
    for (var i = 0; i < alertas.length; i++) {
        var id = alertas[i].p_eventid !== '0' ? alertas[i].p_eventid : alertas[i].eventid;
        if (!problemas[id]) {
            problemas[id] = true;
            ids.push(id);
        }
    }

    var grupos = [];
    if (ids.length) {
        var eventos = chamarApi(p, 'event.get', {
            eventids: ids,
            output: ['eventid', 'name', 'clock', 'severity', 'r_eventid'],
            selectHosts: ['name']
        });
        var rids = eventos.filter(function (e) { return e.r_eventid !== '0'; }).map(function (e) { return e.r_eventid; });
        var recuperacoes = {};
        if (rids.length) {
            chamarApi(p, 'event.get', {eventids: rids, output: ['eventid', 'clock']}).forEach(function (r) {
                recuperacoes[r.eventid] = r.clock;
            });
        }
        grupos = agrupar(eventos, recuperacoes);
    }

    enviarWhatsApp(p, mensagemResumo(grupos, janela, p));
    return 'Resumo enviado: ' + ids.length + ' alerta(s) em ' + grupos.length + ' linha(s)';
}

if (typeof value === 'string') {
    try {
        var params = JSON.parse(value);
        return principal(params, params.agora_ms ? parseInt(params.agora_ms, 10) : Date.now());
    } catch (error) {
        Zabbix.log(3, '[WhatsApp resumo] ' + error);
        throw 'Resumo da manhã falhou: ' + error;
    }
}
