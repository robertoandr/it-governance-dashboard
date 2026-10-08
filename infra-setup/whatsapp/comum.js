// Partes comuns do alerta e do resumo da manhã no WhatsApp.
// Roda no sandbox JS do Zabbix Server (Duktape, ES5): sem let/const, arrow
// functions, template strings nem padStart.

var SEVERIDADES = [
    {emoji: '⚪', nome: 'sem classificação'},
    {emoji: '🔵', nome: 'informativa'},
    {emoji: '🟡', nome: 'atenção'},
    {emoji: '🟠', nome: 'média'},
    {emoji: '🔴', nome: 'alta'},
    {emoji: '🔴', nome: 'crítica'}
];

// Trecho do nome da trigger (inglês, dos templates do Zabbix) → como o
// problema é dito no grupo. `frase` completa o nome do host; `curto` vai
// no resumo da manhã.
var PROBLEMAS = [
    {trecho: 'unavailable by icmp ping', frase: 'está fora do ar (não responde)', curto: 'fora do ar'},
    {trecho: 'high icmp ping response time', frase: 'está com a rede lenta', curto: 'rede lenta'},
    {trecho: 'high icmp ping loss', frase: 'está com a conexão instável (perdendo pacotes)', curto: 'conexão instável'},
    {trecho: 'agent is not available', frase: 'está sem comunicação com o Zabbix', curto: 'sem comunicação'},
    {trecho: 'has been restarted', frase: 'foi reiniciado', curto: 'reiniciado'},
    {trecho: 'high cpu utilization', frase: 'está com o processador sobrecarregado', curto: 'processador sobrecarregado'},
    {trecho: 'high memory utilization', frase: 'está com a memória quase cheia', curto: 'memória quase cheia'},
    {trecho: 'disk space is', frase: 'está com pouco espaço em disco', curto: 'pouco espaço em disco'},
    {trecho: 'ssl certificate', frase: 'tem certificado SSL perto de vencer', curto: 'certificado SSL vencendo'}
];

/** Valor do parâmetro, ou '' se o Zabbix não resolveu a macro. */
function texto(v) {
    if (v === undefined || v === null) {
        return '';
    }
    v = String(v).trim();
    return v.charAt(0) === '{' && v.charAt(v.length - 1) === '}' ? '' : v;
}

function traduzir(nome) {
    var n = String(nome || '').toLowerCase();
    for (var i = 0; i < PROBLEMAS.length; i++) {
        if (n.indexOf(PROBLEMAS[i].trecho) !== -1) {
            return PROBLEMAS[i];
        }
    }
    return {trecho: '', frase: 'teve um alerta: ' + nome, curto: String(nome || 'alerta')};
}

function severidade(n) {
    var i = parseInt(n, 10);
    return SEVERIDADES[i >= 0 && i < SEVERIDADES.length ? i : 0];
}

/** "2h 25m 1s" → "2h 25min"; "5m 1s" → "5 min"; "40s" → "menos de 1 min". */
function duracao(t) {
    var partes = {};
    var re = /(\d+)\s*([dhms])/g;
    var m;
    while ((m = re.exec(String(t || ''))) !== null) {
        partes[m[2]] = parseInt(m[1], 10);
    }
    var d = partes.d || 0, h = partes.h || 0, min = partes.m || 0;
    if (d) {
        return d + (d === 1 ? ' dia' : ' dias') + (h ? ' ' + h + 'h' : '');
    }
    if (h) {
        return h + 'h' + (min ? ' ' + min + 'min' : '');
    }
    if (min) {
        return min + ' min';
    }
    return partes.s !== undefined ? 'menos de 1 min' : '';
}

function dois(n) {
    return (n < 10 ? '0' : '') + n;
}

/** "22:35:06" → "22:35". */
function hhmm(hora) {
    var m = /^(\d{1,2}):(\d{2})/.exec(texto(hora));
    return m ? dois(parseInt(m[1], 10)) + ':' + m[2] : '';
}

/** Data local (fuso fixo, sem horário de verão) de um instante em ms. */
function local(ms, fusoHoras) {
    var d = new Date(ms + fusoHoras * 3600000);
    return {
        ano: d.getUTCFullYear(), mes: d.getUTCMonth(), dia: d.getUTCDate(),
        hora: d.getUTCHours(), minuto: d.getUTCMinutes()
    };
}

/** "08:00" → 480 (minutos desde a meia-noite). */
function minutos(hora) {
    var m = /^(\d{1,2}):(\d{2})$/.exec(String(hora).trim());
    if (!m) {
        throw 'horário inválido: ' + hora;
    }
    return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}

function enviarWhatsApp(p, mensagem) {
    var req = new HttpRequest();
    req.addHeader('Content-Type: application/json');
    req.addHeader('apikey: ' + p.evo_apikey);
    var resp = req.post(
        p.evo_url + '/message/sendText/' + encodeURIComponent(p.instance),
        JSON.stringify({number: p.to, text: mensagem})
    );
    if (req.getStatus() >= 300) {
        throw 'Evolution respondeu HTTP ' + req.getStatus() + ': ' + resp;
    }
}
