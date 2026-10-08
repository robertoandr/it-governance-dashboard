// Item Script "Resumo da manhã no WhatsApp": roda às 08:00 e manda, num bloco
// só, o que o webhook segurou fora do horário (19:00 de ontem → 08:00 de hoje).
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

/** Início e fim (ms) da última noite já encerrada até `agoraMs`. */
function janelaDaNoite(agoraMs, p) {
    var fuso = parseFloat(p.fuso_horas);
    var l = local(agoraMs, fuso);
    var meiaNoite = Date.UTC(l.ano, l.mes, l.dia) - fuso * 3600000;
    var fim = meiaNoite + minutos(p.janela_inicio) * 60000;
    if (agoraMs < fim) {
        fim -= 86400000;
    }
    var inicio = fim - 86400000 + (minutos(p.janela_fim) - minutos(p.janela_inicio)) * 60000;
    return {inicio: inicio, fim: fim};
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

function mensagemResumo(grupos, p) {
    var periodo = '(' + p.janela_fim + ' às ' + p.janela_inicio + ')';
    if (!grupos.length) {
        return '☀️ *Bom dia!* Nenhum alerta durante a noite ' + periodo + '. ✅';
    }
    var ativos = grupos.filter(function (g) { return g.ativo; });
    var resolvidos = grupos.filter(function (g) { return !g.ativo; });
    var linhas = ['☀️ *Bom dia! Resumo dos alertas da noite* ' + periodo];
    linhas = linhas.concat(secao('🔴 *Ainda com problema*', ativos, p));
    linhas = linhas.concat(secao('✅ *Já voltaram ao normal*', resolvidos, p));
    linhas.push('', 'Os chamados no Zendesk foram abertos normalmente durante a noite.');
    return linhas.join('\n');
}

function principal(p, agoraMs) {
    var janela = janelaDaNoite(agoraMs, p);
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

    enviarWhatsApp(p, mensagemResumo(grupos, p));
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
