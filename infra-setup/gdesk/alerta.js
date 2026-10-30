// Webhook GDesk: abre um chamado no GDesk (API publica em {$GDESK.URL})
// a partir de um alerta do Zabbix. ES5 (Duktape): sem let/const/class/=>/includes.
//
// Regras:
//  - So abre chamado em PROBLEMA novo. Recovery e atualizacao/ack sao ignorados
//    (a API publica do GDesk hoje so cria chamado). A baixa continua no Zendesk.
//  - Dedup por tag do evento: se ja existe a tag "gdesk_ticket", nao repete.
//  - Severidade do Zabbix -> prioridade do GDesk (parametros severity_*).
//
// Retorno: JSON com a tag {"gdesk_ticket": "<id>"} na abertura; "{}" quando ignora.

var Gdesk = {
    params: {},

    setParams: function (params) {
        Gdesk.params = params;
        if (typeof Gdesk.params.gdesk_url === 'string') {
            Gdesk.params.gdesk_url = Gdesk.params.gdesk_url.replace(/\/+$/, '');
        }
    },

    prioridade: function () {
        var p = Gdesk.params;
        var mapa = {
            '0': p.severity_not_classified,
            '1': p.severity_information,
            '2': p.severity_warning,
            '3': p.severity_average,
            '4': p.severity_high,
            '5': p.severity_disaster
        };
        var valor = mapa[String(p.severity)];
        return (valor && valor !== '') ? valor : 'normal';
    },

    descricao: function () {
        var p = Gdesk.params;
        var linhas = [];
        linhas.push('Host: ' + (p.host || '-'));
        linhas.push('Severidade: ' + (p.event_severity || '-'));
        if (p.opdata && p.opdata !== '' && p.opdata !== '*UNKNOWN*') {
            linhas.push('Valor: ' + p.opdata);
        }
        linhas.push('Inicio: ' + (p.event_date || '') + ' ' + (p.event_time || '') + ' (ha ' + (p.age || '?') + ')');
        linhas.push('Evento Zabbix #' + (p.event_id || '-'));
        if (p.link) {
            linhas.push(p.link);
        }
        return linhas.join('\n').substring(0, 5000);
    },

    titulo: function () {
        var p = Gdesk.params;
        var t = (p.event_severity ? p.event_severity + ': ' : '') + (p.event_name || 'Alerta Zabbix');
        return t.substring(0, 250);
    },

    criarChamado: function () {
        var p = Gdesk.params;
        var payload = JSON.stringify({
            title: Gdesk.titulo(),
            description: Gdesk.descricao(),
            queue_id: parseInt(p.queue_id, 10) || 2,
            priority: Gdesk.prioridade()
        });

        var req = new HttpRequest();
        req.addHeader('Authorization: Bearer ' + p.gdesk_apikey);
        req.addHeader('Content-Type: application/json');

        var resp = req.post(p.gdesk_url + '/api/public/tickets', payload);
        var status = req.getStatus();

        Zabbix.log(4, '[GDesk] POST /api/public/tickets -> HTTP ' + status + ' resp=' + resp);

        if (status < 200 || status > 299) {
            throw 'HTTP ' + status + ': ' + resp;
        }

        var ticket = '';
        try {
            var data = JSON.parse(resp);
            ticket = data.id || data.ticket_id || data.number ||
                     (data.data ? (data.data.id || data.data.number) : '') || '';
        } catch (e) {
            Zabbix.log(3, '[GDesk] resposta nao-JSON: ' + resp);
        }
        return String(ticket);
    }
};

try {
    var params = JSON.parse(value);
    Gdesk.setParams(params);

    if (!params.gdesk_url || !params.gdesk_apikey) {
        throw 'faltam gdesk_url/gdesk_apikey';
    }

    // So abre chamado em problema novo (event_value=1 e nao e atualizacao/ack).
    if (String(params.event_value) !== '1' || String(params.event_update_status) === '1') {
        return '{}';
    }

    // Dedup: evento que ja tem a tag gdesk_ticket nao abre chamado de novo.
    if (params.event_tags && String(params.event_tags).indexOf('gdesk_ticket') !== -1) {
        return '{}';
    }

    var ticket = Gdesk.criarChamado();
    return JSON.stringify({ tags: { gdesk_ticket: ticket } });
}
catch (error) {
    Zabbix.log(4, '[GDesk] erro: ' + error);
    throw 'GDesk: ' + error;
}
