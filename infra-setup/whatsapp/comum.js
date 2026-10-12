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

// ── Fim de semana e feriados (nacionais + Curitiba/PR) ────────────────────
// Nesses dias o webhook não manda nada na hora: os alertas vão em resumos
// nos horários de `resumos_folga` (padrão 08:00, 14:00 e 18:00).

// Fixos, "DD/MM": nacionais (Lei 662/1949, 6.802/1980 e 14.759/2023) e
// Nossa Senhora da Luz dos Pinhais (padroeira de Curitiba).
var FERIADOS_FIXOS = ['01/01', '21/04', '01/05', '07/09', '08/09', '12/10', '02/11', '15/11', '20/11', '25/12'];

/** Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher) em ms UTC. */
function pascoa(ano) {
    var a = ano % 19, b = Math.floor(ano / 100), c = ano % 100;
    var d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25);
    var g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30;
    var i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7;
    var m = Math.floor((a + 11 * h + 22 * l) / 451);
    var mes = Math.floor((h + l - 7 * m + 114) / 31), dia = ((h + l - 7 * m + 114) % 31) + 1;
    return Date.UTC(ano, mes - 1, dia);
}

/** Feriado nacional ou de Curitiba (mais os extras "DD/MM" ou "AAAA-MM-DD", separados por vírgula). */
function ehFeriado(ano, mes, dia, extras) {
    var ddmm = dois(dia) + '/' + dois(mes + 1);
    var iso = ano + '-' + dois(mes + 1) + '-' + dois(dia);
    var lista = FERIADOS_FIXOS.concat(String(texto(extras)).split(','));
    for (var i = 0; i < lista.length; i++) {
        var f = lista[i].trim();
        if (f === ddmm || f === iso) {
            return true;
        }
    }
    var diasDaPascoa = Math.round((Date.UTC(ano, mes, dia) - pascoa(ano)) / 86400000);
    return diasDaPascoa === -2 || diasDaPascoa === 60; // Sexta-feira Santa e Corpus Christi (Curitiba)
}

/** Sábado, domingo ou feriado (data local de `local()`). */
function ehFolga(l, extras) {
    var semana = new Date(Date.UTC(l.ano, l.mes, l.dia)).getUTCDay();
    return semana === 0 || semana === 6 || ehFeriado(l.ano, l.mes, l.dia, extras);
}

/** "08:00,14:00,18:00" → ['08:00', '14:00', '18:00']. */
function horarios(lista) {
    return String(texto(lista) || '08:00,14:00,18:00').split(',').map(function (h) { return h.trim(); });
}
