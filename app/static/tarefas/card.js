/*
 * Painel do card (/gov/tarefas/b/<board>/c/<card>).
 *
 * - URL própria: abrir/fechar usa history.pushState; o link pode ser colado
 *   em chamados e e-mails.
 * - Documento Markdown com salvamento automático (2 s sem digitar) e
 *   controle de versão: se outra pessoa salvou antes, o texto local nunca é
 *   descartado; o usuário escolhe o que fazer.
 * - Comentários com edição/exclusão conforme permissão devolvida pela API.
 *
 * Texto da API entra via textContent. O único innerHTML é o HTML de Markdown
 * que o servidor já devolve sanitizado (markdown-it + nh3).
 */
(function () {
  'use strict';

  var raiz = document.getElementById('tarefas-board');
  var painel = document.getElementById('card-painel');
  if (!raiz || !painel) return;

  var cfg = raiz.dataset;
  var PODE_EDITAR = cfg.podeEditar === 'true';
  var PODE_COMENTAR = cfg.podeComentar === 'true';
  var ESPERA_SALVAR_MS = 2000;
  var ROTULO = { backlog: 'Backlog', todo: 'To Do', doing: 'Doing', done: 'Done' };
  var ESCRITA = { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };

  function el(id) { return document.getElementById(id); }
  function url(modelo, id) { return modelo.replace(/\/0(?=\/|$)/, '/' + id); }
  function dataHora(iso) {
    return iso ? new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '';
  }
  async function lerJson(r) { try { return await r.json(); } catch (_) { return {}; } }

  var estado = {
    id: null,
    card: null,
    doc: { versao: 0, salvo: '', timer: null, salvando: null, conflito: null },
    usuarios: null,
    origemFoco: null,
  };

  // ── Avisos ────────────────────────────────────────────────────────────────

  function aviso(texto) {
    var p = el('card-aviso');
    p.textContent = texto || '';
    p.hidden = !texto;
  }

  function statusDoc(texto) { var s = el('doc-status'); if (s) s.textContent = texto; }

  async function tratarResposta(r) {
    if (r.status === 401) { aviso('Sua sessão expirou. Recarregue a página e entre novamente.'); return null; }
    return lerJson(r);
  }

  // ── Abrir / fechar ────────────────────────────────────────────────────────

  async function abrir(id, empilhar) {
    if (estado.id && estado.id !== id && !(await fechar(false))) return;
    estado.id = id;
    estado.origemFoco = document.activeElement;
    aviso('');
    painel.hidden = false;
    mostrarAba('documento');
    if (empilhar) history.pushState({ card: id }, '', url(cfg.urlCardPage, id));
    try {
      var respostas = await Promise.all([
        fetch(url(cfg.urlCard, id), { credentials: 'same-origin' }),
        fetch(url(cfg.urlDocumento, id), { credentials: 'same-origin' }),
        fetch(url(cfg.urlComentarios, id), { credentials: 'same-origin' }),
      ]);
      if (respostas[0].status === 404) { aviso('Este card não existe mais ou foi excluído.'); return; }
      var dados = await Promise.all(respostas.map(tratarResposta));
      if (dados.some(function (d) { return d === null; }) || estado.id !== id) return;
      renderizarCard(dados[0]);
      carregarDocumento(dados[1]);
      renderizarComentarios(dados[2].items || []);
      var titulo = el('card-titulo');
      if (titulo) titulo.focus();
    } catch (_) {
      aviso('Não foi possível carregar o card. Verifique a conexão e tente de novo.');
    }
  }

  // Devolve false quando não pode fechar (conflito de versão sem decisão:
  // fechar descartaria o texto local).
  async function fechar(empilhar) {
    if (!estado.id) return true;
    if (estado.doc.conflito && sujo()) {
      aviso('Resolva o conflito do documento antes de fechar: seu texto ainda não foi salvo.');
      if (!empilhar && idDaUrl() !== estado.id) history.pushState({ card: estado.id }, '', url(cfg.urlCardPage, estado.id));
      return false;
    }
    await salvarPendente();
    painel.hidden = true;
    var id = estado.id;
    estado.id = null;
    estado.card = null;
    if (empilhar) history.pushState({}, '', cfg.urlBoardPage);
    var noBoard = raiz.querySelector('.tarefas-card[data-id="' + id + '"]');
    if (noBoard) noBoard.focus();
    else if (estado.origemFoco && estado.origemFoco.isConnected) estado.origemFoco.focus();
    return true;
  }

  function idDaUrl() {
    var m = location.pathname.match(/\/c\/(\d+)\/?$/);
    return m ? Number(m[1]) : null;
  }

  window.addEventListener('popstate', function () {
    var id = idDaUrl();
    if (id) abrir(id, false); else fechar(false);
  });

  el('card-fechar').addEventListener('click', function () { fechar(true); });
  painel.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { e.preventDefault(); fechar(true); }
  });

  el('card-copiar-link').addEventListener('click', async function () {
    var link = location.origin + url(cfg.urlCardPage, estado.id);
    try {
      await navigator.clipboard.writeText(link);
      this.textContent = 'Link copiado';
    } catch (_) {
      this.textContent = link;
    }
    var botao = this;
    setTimeout(function () { botao.textContent = 'Copiar link'; }, 2500);
  });

  // Clique ou Enter num card do board abre o painel.
  raiz.addEventListener('click', function (e) {
    var li = e.target.closest && e.target.closest('.tarefas-card');
    if (!li || e.target.closest('select') || (window.TarefasBoard && window.TarefasBoard.arrastouAgora())) return;
    abrir(Number(li.dataset.id), true);
  });
  raiz.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.altKey || !e.target.classList || !e.target.classList.contains('tarefas-card')) return;
    e.preventDefault();
    abrir(Number(e.target.dataset.id), true);
  });

  // ── Abas ──────────────────────────────────────────────────────────────────

  function mostrarAba(nome) {
    ['documento', 'comentarios'].forEach(function (aba) {
      var ativa = aba === nome;
      var botao = el('aba-' + aba);
      botao.setAttribute('aria-selected', String(ativa));
      botao.classList.toggle('border-blue-600', ativa);
      botao.classList.toggle('border-transparent', !ativa);
      botao.classList.toggle('font-medium', ativa);
      el('card-aba-' + aba).hidden = !ativa;
    });
  }
  el('aba-documento').addEventListener('click', function () { mostrarAba('documento'); });
  el('aba-comentarios').addEventListener('click', function () { mostrarAba('comentarios'); });

  // ── Cabeçalho: título, responsável, histórico ─────────────────────────────

  var ACOES = {
    created: function (a) { return 'criou o card em ' + (ROTULO[a.to_status] || a.to_status); },
    moved: function (a) { return 'moveu de ' + (ROTULO[a.from_status] || a.from_status) + ' para ' + (ROTULO[a.to_status] || a.to_status); },
    edited: function () { return 'editou'; },
    assigned: function () { return 'alterou o responsável'; },
  };

  function renderizarCard(card) {
    estado.card = card;
    el('card-local').textContent = card.workspace.name + ' › ' + card.board.name;
    var titulo = el('card-titulo');
    if (titulo.tagName === 'INPUT') titulo.value = card.title; else titulo.textContent = card.title;
    el('card-status').textContent = ROTULO[card.status] || card.status;
    el('card-criado').textContent = 'Criado por ' + (card.created_by.name || '—') + ' em ' + dataHora(card.created_at);
    renderizarResponsavel(card);

    var lista = el('card-historico');
    while (lista.firstChild) lista.removeChild(lista.firstChild);
    card.activity.forEach(function (a) {
      var li = document.createElement('li');
      li.textContent = dataHora(a.at) + ' · ' + (a.actor || '—') + ' ' + (ACOES[a.action] ? ACOES[a.action](a) : a.action);
      lista.appendChild(li);
    });
  }

  async function renderizarResponsavel(card) {
    var campo = el('card-responsavel');
    if (campo.tagName !== 'SELECT') {
      campo.textContent = card.assignee ? card.assignee.name : 'ninguém';
      return;
    }
    if (!estado.usuarios) {
      try {
        var r = await fetch(cfg.urlUsuarios, { credentials: 'same-origin' });
        var d = await tratarResposta(r);
        estado.usuarios = d && d.items ? d.items : [];
      } catch (_) { estado.usuarios = []; }
    }
    while (campo.firstChild) campo.removeChild(campo.firstChild);
    var nenhum = document.createElement('option');
    nenhum.value = '';
    nenhum.textContent = 'ninguém';
    campo.appendChild(nenhum);
    var opcoes = estado.usuarios.slice();
    if (card.assignee && !opcoes.some(function (u) { return u.id === card.assignee.id; })) {
      opcoes.push({ id: card.assignee.id, name: card.assignee.name + ' (desativado)' });
    }
    opcoes.forEach(function (u) {
      var opt = document.createElement('option');
      opt.value = String(u.id);
      opt.textContent = u.name;
      campo.appendChild(opt);
    });
    campo.value = card.assignee ? String(card.assignee.id) : '';
  }

  async function editarCard(campos) {
    var corpo = Object.assign({ version: estado.card.version }, campos);
    try {
      var r = await fetch(url(cfg.urlCard, estado.id), {
        method: 'PATCH', credentials: 'same-origin', headers: ESCRITA, body: JSON.stringify(corpo),
      });
      var d = await tratarResposta(r);
      if (d === null) return;
      if (r.ok) {
        renderizarCard(d);
        aviso('');
        if (window.TarefasBoard) window.TarefasBoard.recarregar();
        return;
      }
      aviso(d.error || ('Não foi possível salvar (HTTP ' + r.status + ').'));
      var atual = await tratarResposta(await fetch(url(cfg.urlCard, estado.id), { credentials: 'same-origin' }));
      if (atual && atual.id) renderizarCard(atual);
    } catch (_) {
      aviso('Sem conexão com o servidor. A alteração não foi salva.');
      renderizarCard(estado.card);
    }
  }

  if (PODE_EDITAR) {
    var titulo = el('card-titulo');
    titulo.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); titulo.blur(); }
    });
    titulo.addEventListener('blur', function () {
      if (!estado.card) return;
      var novo = titulo.value.trim();
      if (!novo) { titulo.value = estado.card.title; return; }
      if (novo !== estado.card.title) editarCard({ title: novo });
    });
    el('card-responsavel').addEventListener('change', function () {
      var valor = this.value;
      editarCard({ assignee_id: valor ? Number(valor) : null });
    });
  }

  // ── Documento ─────────────────────────────────────────────────────────────

  var texto = el('doc-texto');
  var preview = el('doc-preview');

  function carregarDocumento(doc) {
    estado.doc.versao = doc.version;
    estado.doc.salvo = doc.content_md;
    estado.doc.conflito = null;
    clearTimeout(estado.doc.timer);
    if (texto) {
      texto.value = doc.content_md;
      el('doc-conflito').hidden = true;
      el('doc-descartado').hidden = true;
      modoEditar(true);
      statusDoc(doc.updated_at ? 'Salvo em ' + dataHora(doc.updated_at) + (doc.updated_by ? ' por ' + doc.updated_by : '') : '');
    } else {
      mostrarHtml(doc.html, 'Nenhuma documentação ainda.');
    }
  }

  function mostrarHtml(html, vazio) {
    if (html) {
      preview.innerHTML = html; // HTML já sanitizado pelo servidor (nh3)
    } else {
      preview.textContent = vazio;
    }
  }

  function modoEditar(editar) {
    texto.hidden = !editar;
    el('doc-dica').hidden = !editar;
    preview.hidden = editar;
    el('doc-modo-editar').setAttribute('aria-pressed', String(editar));
    el('doc-modo-ver').setAttribute('aria-pressed', String(!editar));
    el('doc-modo-editar').classList.toggle('bg-slate-100', editar);
    el('doc-modo-ver').classList.toggle('bg-slate-100', !editar);
  }

  async function visualizar() {
    modoEditar(false);
    preview.textContent = 'Gerando pré-visualização…';
    try {
      var r = await fetch(cfg.urlPreview, {
        method: 'POST', credentials: 'same-origin', headers: ESCRITA, body: JSON.stringify({ content_md: texto.value }),
      });
      var d = await tratarResposta(r);
      if (d === null) return;
      if (!r.ok) { preview.textContent = d.error || 'Não foi possível gerar a pré-visualização.'; return; }
      mostrarHtml(d.html, 'Documento vazio.');
    } catch (_) {
      preview.textContent = 'Sem conexão com o servidor.';
    }
  }

  function sujo() { return texto && estado.id && texto.value !== estado.doc.salvo; }

  function agendarSalvar() {
    clearTimeout(estado.doc.timer);
    if (estado.doc.conflito) return;
    statusDoc('Alterações não salvas');
    estado.doc.timer = setTimeout(salvar, ESPERA_SALVAR_MS);
  }

  async function salvar() {
    clearTimeout(estado.doc.timer);
    if (!sujo() || estado.doc.conflito) return;
    if (estado.doc.salvando) { await estado.doc.salvando; return salvar(); }
    var cardId = estado.id;
    var enviado = texto.value;
    statusDoc('Salvando…');
    estado.doc.salvando = (async function () {
      try {
        var r = await fetch(url(cfg.urlDocumento, cardId), {
          method: 'PUT', credentials: 'same-origin', headers: ESCRITA,
          body: JSON.stringify({ content_md: enviado, version: estado.doc.versao }),
        });
        var d = await tratarResposta(r);
        if (d === null || estado.id !== cardId) return;
        if (r.ok) {
          estado.doc.versao = d.version;
          estado.doc.salvo = enviado;
          if (texto.value === enviado) statusDoc('Salvo às ' + new Date().toLocaleTimeString('pt-BR'));
          else agendarSalvar();
          if (window.TarefasBoard) window.TarefasBoard.recarregar();
          return;
        }
        if (r.status === 409 && d.current) { mostrarConflito(d.current); return; }
        statusDoc(d.error || ('Erro ao salvar (HTTP ' + r.status + '). Seu texto continua aqui.'));
      } catch (_) {
        statusDoc('Sem conexão. Seu texto continua aqui; nova tentativa em 5 s.');
        estado.doc.timer = setTimeout(salvar, 5000);
      }
    })();
    try { await estado.doc.salvando; } finally { estado.doc.salvando = null; }
  }

  async function salvarPendente() {
    if (sujo() && !estado.doc.conflito) await salvar();
  }

  function mostrarConflito(atual) {
    estado.doc.conflito = atual;
    statusDoc('Não salvo: conflito de versão');
    el('doc-conflito-texto').textContent = (atual.updated_by || 'Outra pessoa') +
      ' salvou este documento em ' + dataHora(atual.updated_at) +
      ' enquanto você editava. Seu texto continua no editor e nada foi perdido.';
    el('doc-atual').hidden = true;
    el('doc-conflito').hidden = false;
  }

  if (texto) {
    texto.addEventListener('input', agendarSalvar);
    texto.addEventListener('blur', function () { if (sujo()) salvar(); });
    el('doc-modo-editar').addEventListener('click', function () { modoEditar(true); texto.focus(); });
    el('doc-modo-ver').addEventListener('click', visualizar);

    el('doc-ver-atual').addEventListener('click', function () {
      var caixa = el('doc-atual');
      caixa.hidden = !caixa.hidden;
      if (!caixa.hidden) {
        if (estado.doc.conflito.html) caixa.innerHTML = estado.doc.conflito.html; // sanitizado pelo servidor
        else caixa.textContent = '(documento vazio)';
      }
    });
    el('doc-manter-meu').addEventListener('click', function () {
      // Decisão explícita do usuário: grava por cima da versão salva.
      estado.doc.versao = estado.doc.conflito.version;
      estado.doc.conflito = null;
      el('doc-conflito').hidden = true;
      salvar();
    });
    el('doc-usar-atual').addEventListener('click', function () {
      var meu = texto.value;
      var atual = estado.doc.conflito;
      carregarDocumento(atual);
      if (meu !== atual.content_md) {
        el('doc-descartado-texto').value = meu; // o texto local fica disponível para copiar
        el('doc-descartado').hidden = false;
      }
    });

    window.addEventListener('beforeunload', function (e) {
      if (sujo()) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  // ── Comentários ───────────────────────────────────────────────────────────

  function atualizarContagem(n) {
    el('com-contagem').textContent = n ? '(' + n + ')' : '';
  }

  function renderizarComentarios(itens) {
    var lista = el('com-lista');
    while (lista.firstChild) lista.removeChild(lista.firstChild);
    if (!itens.length) {
      var vazio = document.createElement('li');
      vazio.className = 'text-sm text-slate-500 dark:text-slate-400';
      vazio.dataset.vazio = 'true';
      vazio.textContent = 'Nenhum comentário ainda.';
      lista.appendChild(vazio);
    }
    itens.forEach(function (c) { lista.appendChild(elementoComentario(c)); });
    atualizarContagem(itens.length);
  }

  function botaoPequeno(rotulo, classe) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'hover:underline ' + (classe || '');
    b.textContent = rotulo;
    return b;
  }

  function elementoComentario(c) {
    var li = document.createElement('li');
    li.className = 'rounded-lg border border-slate-200 dark:border-slate-700 p-3 space-y-1';
    li.dataset.id = String(c.id);

    var topo = document.createElement('div');
    topo.className = 'flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400';
    var autor = document.createElement('strong');
    autor.className = 'text-slate-700 dark:text-slate-200';
    autor.textContent = c.author.name || '—';
    var quando = document.createElement('span');
    quando.textContent = dataHora(c.created_at) + (c.edited_at ? ' (editado)' : '');
    topo.appendChild(autor);
    topo.appendChild(quando);

    var acoes = document.createElement('span');
    acoes.className = 'ml-auto flex gap-3';
    if (c.can_edit) {
      var editar = botaoPequeno('Editar', 'text-blue-600 dark:text-blue-400');
      editar.addEventListener('click', function () { editarComentario(li, c); });
      acoes.appendChild(editar);
    }
    if (c.can_delete) {
      var excluir = botaoPequeno('Excluir', 'text-red-600 dark:text-red-400');
      excluir.addEventListener('click', function () { confirmarExclusao(li, c, acoes); });
      acoes.appendChild(excluir);
    }
    topo.appendChild(acoes);

    var corpo = document.createElement('div');
    corpo.className = 'tarefas-md text-sm text-slate-800 dark:text-slate-100';
    corpo.innerHTML = c.html; // sanitizado pelo servidor (nh3)
    li.appendChild(topo);
    li.appendChild(corpo);
    return li;
  }

  function confirmarExclusao(li, c, acoes) {
    var original = Array.prototype.slice.call(acoes.childNodes);
    while (acoes.firstChild) acoes.removeChild(acoes.firstChild);
    var pergunta = document.createElement('span');
    pergunta.textContent = 'Excluir?';
    var sim = botaoPequeno('Sim, excluir', 'text-red-600 dark:text-red-400 font-medium');
    var nao = botaoPequeno('Cancelar');
    nao.addEventListener('click', function () {
      while (acoes.firstChild) acoes.removeChild(acoes.firstChild);
      original.forEach(function (n) { acoes.appendChild(n); });
    });
    sim.addEventListener('click', async function () {
      try {
        var r = await fetch(url(cfg.urlComentario, c.id), { method: 'DELETE', credentials: 'same-origin', headers: ESCRITA });
        if (r.status === 204) { await recarregarComentarios(); return; }
        var d = await tratarResposta(r);
        if (d) aviso(d.error || ('Não foi possível excluir (HTTP ' + r.status + ').'));
      } catch (_) {
        aviso('Sem conexão com o servidor. O comentário não foi excluído.');
      }
    });
    acoes.appendChild(pergunta);
    acoes.appendChild(sim);
    acoes.appendChild(nao);
    sim.focus();
  }

  function editarComentario(li, c) {
    var corpo = li.lastChild;
    var form = document.createElement('form');
    form.className = 'space-y-2';
    var campo = document.createElement('textarea');
    campo.rows = 3;
    campo.maxLength = 10000;
    campo.value = c.body_md;
    campo.setAttribute('aria-label', 'Editar comentário');
    campo.className = 'w-full rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-950 p-2 text-sm text-slate-800 dark:text-slate-100';
    var salvarBtn = document.createElement('button');
    salvarBtn.type = 'submit';
    salvarBtn.className = 'rounded bg-blue-600 hover:bg-blue-700 text-white text-xs px-2 py-1';
    salvarBtn.textContent = 'Salvar';
    var cancelar = botaoPequeno('Cancelar', 'text-xs ml-2');
    form.appendChild(campo);
    form.appendChild(salvarBtn);
    form.appendChild(cancelar);
    li.replaceChild(form, corpo);
    campo.focus();
    cancelar.addEventListener('click', function () { li.replaceChild(corpo, form); });
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      try {
        var r = await fetch(url(cfg.urlComentario, c.id), {
          method: 'PATCH', credentials: 'same-origin', headers: ESCRITA, body: JSON.stringify({ body_md: campo.value }),
        });
        var d = await tratarResposta(r);
        if (d === null) return;
        if (!r.ok) { aviso(d.error || ('Não foi possível salvar (HTTP ' + r.status + ').')); return; }
        li.replaceWith(elementoComentario(d));
        aviso('');
      } catch (_) {
        aviso('Sem conexão com o servidor. O comentário não foi alterado.');
      }
    });
  }

  async function recarregarComentarios() {
    var r = await fetch(url(cfg.urlComentarios, estado.id), { credentials: 'same-origin' });
    var d = await tratarResposta(r);
    if (d && d.items) renderizarComentarios(d.items);
    if (window.TarefasBoard) window.TarefasBoard.recarregar();
  }

  if (PODE_COMENTAR) {
    var form = el('com-form');
    var campo = el('com-texto');
    campo.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      var corpo = campo.value.trim();
      if (!corpo) { campo.focus(); return; }
      campo.disabled = true;
      try {
        var r = await fetch(url(cfg.urlComentarios, estado.id), {
          method: 'POST', credentials: 'same-origin', headers: ESCRITA, body: JSON.stringify({ body_md: corpo }),
        });
        var d = await tratarResposta(r);
        if (d === null) return;
        if (!r.ok) { aviso(d.error || ('Não foi possível comentar (HTTP ' + r.status + ').')); return; }
        campo.value = '';
        aviso('');
        var lista = el('com-lista');
        var vazio = lista.querySelector('[data-vazio]');
        if (vazio) vazio.remove();
        lista.appendChild(elementoComentario(d));
        atualizarContagem(lista.children.length);
        if (window.TarefasBoard) window.TarefasBoard.recarregar();
      } catch (_) {
        aviso('Sem conexão com o servidor. O comentário não foi enviado; o texto continua no campo.');
      } finally {
        campo.disabled = false;
        campo.focus();
      }
    });
  }

  // ── Início: URL com /c/<id> já abre o card ────────────────────────────────

  if (cfg.cardAberto) abrir(Number(cfg.cardAberto), false);
})();
