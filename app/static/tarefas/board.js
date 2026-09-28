/*
 * Kanban do módulo Tarefas (/gov/tarefas/b/<id>).
 *
 * - Carrega os cards pela API e redesenha as 4 colunas.
 * - Arrastar e soltar (SortableJS) ou teclado (Alt + setas, ou o menu
 *   "Mover para") com atualização otimista: o card muda de lugar na hora e
 *   volta se a API recusar. Em 409 o board é recarregado.
 * - Atualização automática: consulta a revisão do board a cada 15 s, com a
 *   aba visível, e só recarrega os cards quando ela muda (ADR 0008).
 *
 * Todo texto vindo da API entra via textContent (nunca innerHTML).
 */
(function () {
  'use strict';

  var raiz = document.getElementById('tarefas-board');
  if (!raiz) return;

  var URL_CARDS = raiz.dataset.urlCards;
  var URL_REVISAO = raiz.dataset.urlRevision;
  var URL_MOVER = raiz.dataset.urlMove; // .../cards/0/move
  var PODE_EDITAR = raiz.dataset.podeEditar === 'true';
  var INTERVALO_MS = 15000;
  var STATUS = ['backlog', 'todo', 'doing', 'done'];
  var ROTULO = { backlog: 'Backlog', todo: 'To Do', doing: 'Doing', done: 'Done' };
  var CABECALHOS_ESCRITA = { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };

  var estado = { revisao: null, cards: {}, ocupado: 0, arrastando: false, sessaoExpirada: false };
  var listas = {};
  STATUS.forEach(function (s) { listas[s] = raiz.querySelector('ol[data-status="' + s + '"]'); });

  var elSync = document.getElementById('tarefas-sync');
  var elAviso = document.getElementById('tarefas-aviso');
  var elAnuncio = document.getElementById('tarefas-anuncio');

  // ── Utilidades ────────────────────────────────────────────────────────────

  function urlMover(id) { return URL_MOVER.replace('/cards/0/', '/cards/' + id + '/'); }

  function mostrarAviso(texto) { elAviso.textContent = texto; elAviso.hidden = false; }
  function esconderAviso() { elAviso.hidden = true; elAviso.textContent = ''; }
  function anunciar(texto) { elAnuncio.textContent = ''; setTimeout(function () { elAnuncio.textContent = texto; }, 50); }

  function marcarAtualizado() {
    var agora = new Date();
    elSync.textContent = 'Atualizado às ' + agora.toLocaleTimeString('pt-BR');
  }

  function sessaoExpirou() {
    estado.sessaoExpirada = true;
    mostrarAviso('Sua sessão expirou. Recarregue a página e entre novamente.');
  }

  async function lerJson(resposta) {
    try { return await resposta.json(); } catch (_) { return {}; }
  }

  function atualizarContagens() {
    STATUS.forEach(function (s) {
      var el = raiz.querySelector('[data-contagem="' + s + '"]');
      if (el) el.textContent = String(listas[s].children.length);
    });
  }

  function rotuloCard(card) {
    var texto = card.title + '. Coluna ' + ROTULO[card.status] + '.';
    return PODE_EDITAR ? texto + ' Alt e setas para mover.' : texto;
  }

  // ── Desenho ───────────────────────────────────────────────────────────────

  function criarElemento(card) {
    var li = document.createElement('li');
    li.className = 'tarefas-card rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 ' +
      'p-3 shadow-sm space-y-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500' +
      (PODE_EDITAR ? ' cursor-grab active:cursor-grabbing' : '');
    li.tabIndex = 0;
    li.dataset.id = String(card.id);
    li.setAttribute('aria-label', rotuloCard(card));

    var titulo = document.createElement('p');
    titulo.className = 'text-sm text-slate-800 dark:text-slate-100 break-words';
    titulo.textContent = card.title;
    li.appendChild(titulo);

    var meta = document.createElement('div');
    meta.className = 'flex items-center gap-3 text-[11px] text-slate-500 dark:text-slate-400';
    if (card.assignee) {
      var resp = document.createElement('span');
      resp.textContent = card.assignee.name;
      meta.appendChild(resp);
    }
    if (card.has_document) {
      var doc = document.createElement('span');
      doc.textContent = 'Documentado';
      meta.appendChild(doc);
    }
    if (card.comment_count) {
      var com = document.createElement('span');
      com.textContent = card.comment_count + (card.comment_count === 1 ? ' comentário' : ' comentários');
      meta.appendChild(com);
    }
    if (meta.children.length) li.appendChild(meta);

    if (PODE_EDITAR) {
      var linha = document.createElement('div');
      linha.className = 'tarefas-mover-linha justify-end';
      var sel = document.createElement('select');
      sel.className = 'rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 ' +
        'text-[11px] py-0.5 text-slate-700 dark:text-slate-200';
      sel.setAttribute('aria-label', 'Mover para');
      STATUS.forEach(function (s) {
        var opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s === card.status ? ROTULO[s] + ' (atual)' : 'Mover para ' + ROTULO[s];
        opt.selected = s === card.status;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', function () { moverParaColuna(li, sel.value); });
      linha.appendChild(sel);
      li.appendChild(linha);
    }

    if (PODE_EDITAR) li.addEventListener('keydown', aoTeclar);
    return li;
  }

  function renderizar(colunas, destacar) {
    var anterior = {};
    Object.keys(estado.cards).forEach(function (id) { anterior[id] = estado.cards[id].status; });
    var focado = document.activeElement && document.activeElement.closest &&
      document.activeElement.closest('.tarefas-card');
    var idFocado = focado ? focado.dataset.id : null;

    estado.cards = {};
    STATUS.forEach(function (s) {
      var ol = listas[s];
      while (ol.firstChild) ol.removeChild(ol.firstChild);
      (colunas[s] || []).forEach(function (card) {
        estado.cards[card.id] = card;
        var li = criarElemento(card);
        if (destacar && anterior[card.id] && anterior[card.id] !== card.status) li.classList.add('tarefas-destaque');
        ol.appendChild(li);
      });
    });
    atualizarContagens();
    if (idFocado) {
      var novo = raiz.querySelector('.tarefas-card[data-id="' + idFocado + '"]');
      if (novo) novo.focus();
    }
  }

  // ── Carga e atualização automática ────────────────────────────────────────

  async function carregar(destacar) {
    var r = await fetch(URL_CARDS, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (r.status === 401) { sessaoExpirou(); return; }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    estado.revisao = d.board.revision;
    renderizar(d.columns, destacar);
    marcarAtualizado();
  }

  async function verificar() {
    if (document.hidden || estado.ocupado > 0 || estado.arrastando || estado.sessaoExpirada) return;
    try {
      var r = await fetch(URL_REVISAO, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (r.status === 401) { sessaoExpirou(); return; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var d = await r.json();
      if (d.revision !== estado.revisao) await carregar(true);
      else marcarAtualizado();
      if (elAviso.dataset.tipo === 'conexao') { esconderAviso(); delete elAviso.dataset.tipo; }
    } catch (_) {
      elAviso.dataset.tipo = 'conexao';
      mostrarAviso('Não foi possível atualizar o board. Nova tentativa em 15 segundos.');
    }
  }

  // ── Movimentação ──────────────────────────────────────────────────────────

  function vizinhos(li) {
    var antes = li.previousElementSibling;
    var depois = li.nextElementSibling;
    return {
      before_id: antes ? Number(antes.dataset.id) : null,
      after_id: depois ? Number(depois.dataset.id) : null,
    };
  }

  // Movimentos vão para o servidor um de cada vez: o segundo precisa da
  // versão do card devolvida pelo primeiro (senão vira um 409 falso).
  var fila = Promise.resolve();

  function enviarMovimento(li, status, desfazer) {
    atualizarContagens();
    estado.ocupado++;
    fila = fila.then(function () { return enviarAgora(li, status, desfazer); })
      .finally(function () { estado.ocupado--; });
  }

  function atualizarElemento(li, card) {
    var novo = criarElemento(card);
    li.setAttribute('aria-label', novo.getAttribute('aria-label'));
    li.replaceChildren.apply(li, Array.prototype.slice.call(novo.childNodes));
  }

  async function enviarAgora(li, status, desfazer) {
    if (!li.isConnected) return; // o board foi recarregado enquanto esperava na fila
    var id = Number(li.dataset.id);
    var card = estado.cards[id];
    if (!card) return;
    var v = vizinhos(li);
    try {
      var r = await fetch(urlMover(id), {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: CABECALHOS_ESCRITA,
        body: JSON.stringify({ status: status, before_id: v.before_id, after_id: v.after_id, version: card.version }),
      });
      var d = await lerJson(r);
      if (r.ok) {
        // Só avança a revisão local se ninguém mais escreveu no meio;
        // caso contrário a próxima verificação recarrega o board.
        if (estado.revisao !== null && d.board_revision === estado.revisao + 1) estado.revisao = d.board_revision;
        card.status = d.status;
        card.position = d.position;
        card.version = d.version;
        atualizarElemento(li, card);
        esconderAviso();
        anunciar('"' + card.title + '" movido para ' + ROTULO[card.status] + '.');
        return;
      }
      if (r.status === 401) { desfazer(); atualizarContagens(); sessaoExpirou(); return; }
      if (r.status === 409) {
        mostrarAviso((d.error || 'O board mudou.') + ' O board foi atualizado.');
        await carregar(false);
        return;
      }
      desfazer();
      atualizarContagens();
      mostrarAviso(d.error || ('Não foi possível mover o card (HTTP ' + r.status + ').'));
    } catch (_) {
      desfazer();
      atualizarContagens();
      mostrarAviso('Sem conexão com o servidor. O card voltou para a posição anterior.');
    }
  }

  function moverElemento(li, destino, referencia) {
    var pai = li.parentNode;
    var proximo = li.nextSibling;
    destino.insertBefore(li, referencia);
    li.focus();
    return function desfazer() { pai.insertBefore(li, proximo); };
  }

  function moverParaColuna(li, status) {
    var destino = listas[status];
    if (!destino || li.parentNode === destino) return;
    var desfazer = moverElemento(li, destino, destino.firstElementChild);
    enviarMovimento(li, status, desfazer);
  }

  function aoTeclar(evento) {
    if (!evento.altKey || evento.target !== evento.currentTarget) return;
    var li = evento.currentTarget;
    var status = li.parentNode.dataset.status;
    var i = STATUS.indexOf(status);
    var desfazer = null;

    if (evento.key === 'ArrowUp' && li.previousElementSibling) {
      desfazer = moverElemento(li, li.parentNode, li.previousElementSibling);
    } else if (evento.key === 'ArrowDown' && li.nextElementSibling) {
      desfazer = moverElemento(li, li.parentNode, li.nextElementSibling.nextElementSibling);
    } else if (evento.key === 'ArrowLeft' && i > 0) {
      evento.preventDefault();
      moverParaColuna(li, STATUS[i - 1]);
      return;
    } else if (evento.key === 'ArrowRight' && i < STATUS.length - 1) {
      evento.preventDefault();
      moverParaColuna(li, STATUS[i + 1]);
      return;
    }
    if (desfazer) {
      evento.preventDefault();
      enviarMovimento(li, status, desfazer);
    }
  }

  function iniciarArrastar() {
    if (!PODE_EDITAR || !window.Sortable) return;
    STATUS.forEach(function (s) {
      window.Sortable.create(listas[s], {
        group: 'tarefas',
        animation: 150,
        filter: 'select',
        preventOnFilter: false,
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        onStart: function () { estado.arrastando = true; },
        onEnd: function (evt) {
          estado.arrastando = false;
          if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return;
          var item = evt.item;
          enviarMovimento(item, evt.to.dataset.status, function desfazer() {
            item.remove();
            evt.from.insertBefore(item, evt.from.children[evt.oldIndex] || null);
          });
        },
      });
    });
  }

  // ── Criação rápida ────────────────────────────────────────────────────────

  function iniciarCriacao() {
    if (!PODE_EDITAR) return;
    raiz.querySelectorAll('[data-abrir-criar]').forEach(function (botao) {
      var status = botao.dataset.abrirCriar;
      var form = raiz.querySelector('form[data-criar="' + status + '"]');
      var campo = form.querySelector('textarea');

      function fechar() { form.hidden = true; botao.hidden = false; campo.value = ''; botao.focus(); }

      botao.addEventListener('click', function () { form.hidden = false; botao.hidden = true; campo.focus(); });
      form.querySelector('[data-cancelar]').addEventListener('click', fechar);
      campo.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { e.preventDefault(); fechar(); }
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
      });
      form.addEventListener('submit', async function (e) {
        e.preventDefault();
        var titulo = campo.value.trim();
        if (!titulo) { campo.focus(); return; }
        campo.disabled = true;
        try {
          var r = await fetch(URL_CARDS, {
            method: 'POST',
            credentials: 'same-origin',
            headers: CABECALHOS_ESCRITA,
            body: JSON.stringify({ title: titulo, status: status }),
          });
          var d = await lerJson(r);
          if (r.status === 401) { sessaoExpirou(); return; }
          if (!r.ok) { mostrarAviso(d.error || ('Não foi possível criar o card (HTTP ' + r.status + ').')); return; }
          campo.value = '';
          esconderAviso();
          await carregar(false);
          anunciar('Card "' + d.title + '" criado em ' + ROTULO[status] + '.');
        } catch (_) {
          mostrarAviso('Sem conexão com o servidor. O card não foi criado.');
        } finally {
          campo.disabled = false;
          campo.focus();
        }
      });
    });
  }

  // ── Início ────────────────────────────────────────────────────────────────

  iniciarArrastar();
  iniciarCriacao();
  carregar(false).catch(function () {
    elSync.textContent = '';
    mostrarAviso('Não foi possível carregar o board. Recarregue a página.');
  });
  setInterval(verificar, INTERVALO_MS);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) verificar(); });
})();
