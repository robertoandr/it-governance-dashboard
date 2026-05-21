/* ============================================================
   maintenance.js — Sprint 6d
   UI de Manutenção Manual (FAB, Modal, Toast, Card, Enhance)
   ============================================================ */
(function() {
  'use strict';

  const API = {
    list:    '/api/maintenance',
    history: '/api/maintenance/history',
    mark:    '/api/maintenance/mark',
    clear:   '/api/maintenance/clear',
  };

  const REFRESH_MS = 30000;
  let _activeHosts = [];   // cache do último GET
  let _refreshTimer = null;

  // ── Util: escape HTML ─────────────────────────────────────
  function esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Toast ─────────────────────────────────────────────────
  function toast(msg, type = 'info', ttl = 3500) {
    const el = document.createElement('div');
    el.className = `maint-toast ${type}`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 300);
    }, ttl);
  }

  // ── Fetch JSON com tratamento ─────────────────────────────
  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    let data;
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok || data.ok === false) {
      const msg = data.message || data.error || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  // ── Refresh: busca hosts ativos e atualiza card ───────────
  async function refresh() {
    try {
      const data = await api(API.list);
      // 🛡️ Sprint 6e-fix: normaliza dict {host: info} → array [{host, ...info}]
      const raw = data.hosts;
      if (Array.isArray(raw)) {
        _activeHosts = raw;
      } else if (raw && typeof raw === 'object') {
        _activeHosts = Object.entries(raw).map(([host, info]) => ({
          host,
          ...(info && typeof info === 'object' ? info : {})
        }));
      } else {
        _activeHosts = [];
      }
      renderCard();
    } catch (e) {
      console.warn('[maint] refresh falhou:', e.message);
    }
  }

  // ── Render do card no overview ────────────────────────────
  function renderCard() {
    const card = document.getElementById('maint-active-card');
    if (!card) return;

    const hosts = _activeHosts;
    if (!hosts.length) {
      card.classList.remove('has-items');
      card.innerHTML = '';
      return;
    }

    card.classList.add('has-items');
    card.innerHTML = `
      <div class="maint-card-header">
        <span class="maint-card-title">🔧 Hosts em Manutenção Manual</span>
        <span class="maint-card-count">${hosts.length}</span>
      </div>
      <div class="maint-card-list">
        ${hosts.map(h => `
          <div class="maint-card-item">
            <span class="host-name">${esc(h.host || h.name || '?')}</span>
            ${h.reason ? `<span class="host-reason">— ${esc(h.reason)}</span>` : ''}
            <button class="maint-btn-clear"
                    data-host="${esc(h.host || h.name)}"
                    title="Liberar este host">
              Liberar
            </button>
          </div>
        `).join('')}
      </div>
    `;

    // Liga handlers dos botões "Liberar"
    card.querySelectorAll('.maint-btn-clear').forEach(btn => {
      btn.addEventListener('click', () => {
        const host = btn.dataset.host;
        openClearModal(host);
      });
    });
  }

  // ── Modal: criar estrutura DOM ────────────────────────────
  function ensureModal() {
    if (document.getElementById('maint-modal-overlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'maint-modal-overlay';
    overlay.className = 'maint-modal-overlay';
    overlay.innerHTML = `
      <div class="maint-modal" role="dialog" aria-modal="true">
        <h3 id="maint-modal-title">🔧 Manutenção</h3>
        <form id="maint-modal-form">
          <div id="maint-modal-fields"></div>
          <div class="maint-modal-actions">
            <button type="button" class="maint-btn-cancel" id="maint-btn-cancel">Cancelar</button>
            <button type="submit" class="maint-btn-submit" id="maint-btn-submit">Confirmar</button>
          </div>
        </form>
      </div>
    `;
    document.body.appendChild(overlay);

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeModal();
    });
    document.getElementById('maint-btn-cancel').addEventListener('click', closeModal);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && overlay.classList.contains('show')) closeModal();
    });
  }

  function closeModal() {
    const ov = document.getElementById('maint-modal-overlay');
    if (ov) ov.classList.remove('show');
  }

  // ── Modal: MARK ───────────────────────────────────────────
  function openMarkModal() {
    ensureModal();
    document.getElementById('maint-modal-title').innerHTML = '🔧 Marcar Manutenção';
    document.getElementById('maint-modal-fields').innerHTML = `
      <label>Hosts <span style="color:#f44336">*</span></label>
      <textarea id="mk-hosts" placeholder="CAM-8A-35&#10;CAM-8A-36&#10;(um por linha)" required></textarea>
      <div class="maint-modal-hint">Nomes exatos como no Zabbix, um por linha.</div>

      <label>Operador <span style="color:#f44336">*</span></label>
      <input type="text" id="mk-operator" placeholder="roberto" required
             value="${esc(localStorage.getItem('maint_operator') || '')}">

      <label>Motivo <span style="color:#f44336">*</span></label>
      <input type="text" id="mk-reason" placeholder="Switch 8A em troca" required>

      <label>Domínio</label>
      <select id="mk-domain">
        <option value="cftv">CFTV</option>
        <option value="network">Network</option>
        <option value="server">Server</option>
        <option value="other">Outro</option>
      </select>

      <label>OPS PIN <span style="color:#f44336">*</span></label>
      <input type="password" id="mk-pin" placeholder="••••" required autocomplete="off">
    `;

    const form = document.getElementById('maint-modal-form');
    form.onsubmit = async (e) => {
      e.preventDefault();
      await submitMark();
    };

    document.getElementById('maint-modal-overlay').classList.add('show');
    setTimeout(() => document.getElementById('mk-hosts').focus(), 100);
  }

  async function submitMark() {
    const hostsRaw = document.getElementById('mk-hosts').value.trim();
    const operator = document.getElementById('mk-operator').value.trim();
    const reason = document.getElementById('mk-reason').value.trim();
    const domain = document.getElementById('mk-domain').value;
    const pin = document.getElementById('mk-pin').value;

    const hosts = hostsRaw.split('\n').map(s => s.trim()).filter(Boolean);
    if (!hosts.length) return toast('Informe ao menos 1 host', 'error');
    if (!operator || !reason || !pin) return toast('Preencha todos os campos', 'error');

    const btn = document.getElementById('maint-btn-submit');
    btn.disabled = true;
    btn.textContent = 'Enviando...';

    try {
      const r = await api(API.mark, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Ops-Pin': pin },
        body: JSON.stringify({ hosts, operator, reason, domain }),
      });
      localStorage.setItem('maint_operator', operator);
      toast(`✅ ${hosts.length} host(s) marcado(s)`, 'success');
      closeModal();
      await refresh();
    } catch (e) {
      toast(`❌ ${e.message}`, 'error', 5000);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Confirmar';
    }
  }

  // ── Modal: CLEAR ──────────────────────────────────────────
  function openClearModal(host) {
    ensureModal();
    document.getElementById('maint-modal-title').innerHTML = '✅ Liberar Manutenção';
    document.getElementById('maint-modal-fields').innerHTML = `
      <label>Host</label>
      <input type="text" id="cl-host" value="${esc(host)}" readonly
             style="background:#0a0d18;opacity:0.7">

      <label>Operador <span style="color:#f44336">*</span></label>
      <input type="text" id="cl-operator" required
             value="${esc(localStorage.getItem('maint_operator') || '')}">

      <label>Observação</label>
      <input type="text" id="cl-note" placeholder="Switch normalizado (opcional)">

      <label>OPS PIN <span style="color:#f44336">*</span></label>
      <input type="password" id="cl-pin" required autocomplete="off">
    `;

    const form = document.getElementById('maint-modal-form');
    form.onsubmit = async (e) => {
      e.preventDefault();
      await submitClear(host);
    };

    document.getElementById('maint-modal-overlay').classList.add('show');
    setTimeout(() => document.getElementById('cl-pin').focus(), 100);
  }

  async function submitClear(host) {
    const operator = document.getElementById('cl-operator').value.trim();
    const note = document.getElementById('cl-note').value.trim();
    const pin = document.getElementById('cl-pin').value;

    if (!operator || !pin) return toast('Preencha operador e PIN', 'error');

    const btn = document.getElementById('maint-btn-submit');
    btn.disabled = true;
    btn.textContent = 'Enviando...';

    try {
      await api(API.clear, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Ops-Pin': pin },
        body: JSON.stringify({ hosts: [host], operator, note }),
      });
      localStorage.setItem('maint_operator', operator);
      toast(`✅ ${host} liberado`, 'success');
      closeModal();
      await refresh();
    } catch (e) {
      toast(`❌ ${e.message}`, 'error', 5000);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Confirmar';
    }
  }

  // ── FAB ────────────────────────────────────────────────────
  function ensureFAB() {
    if (document.getElementById('maint-fab')) return;
    const btn = document.createElement('button');
    btn.id = 'maint-fab';
    btn.className = 'maint-fab';
    btn.title = 'Marcar host em manutenção (Ctrl+M)';
    btn.innerHTML = '🔧';
    btn.addEventListener('click', openMarkModal);
    document.body.appendChild(btn);

    // Atalho Ctrl+M
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.key === 'm') {
        e.preventDefault();
        openMarkModal();
      }
    });
  }

  // ── API pública ───────────────────────────────────────────

  // ═══ SPRINT-6e-PATCH3:RELEASE_ALL:BEGIN ═══
  // ── Modal: RELEASE ALL (libera todos os hosts ativos) ────
  function openReleaseAllModal() {
    const count = _activeHosts.length;
    if (count === 0) {
      return toast('Nenhum host em manutenção para liberar', 'info');
    }

    ensureModal();
    document.getElementById('maint-modal-title').innerHTML = '🚨 Liberar TODOS os Hosts';

    const hostsPreview = _activeHosts
      .slice(0, 8)
      .map(function (h) {
        return '<li>' + esc(h.host || h.name || String(h)) + '</li>';
      })
      .join('');
    const moreCount = count > 8 ? count - 8 : 0;

    document.getElementById('maint-modal-fields').innerHTML =
      '<div class="maint-modal-hint" style="background:rgba(244,67,54,0.08);border-left:4px solid #f44336;padding:10px 12px;border-radius:6px;margin-bottom:12px">' +
        '<strong style="color:#f44336">⚠️ Ação destrutiva e irreversível</strong><br>' +
        'Esta operação vai remover <strong>' + count + ' host(s)</strong> da manutenção de uma só vez.' +
      '</div>' +
      '<label>Hosts que serão liberados</label>' +
      '<ul style="margin:4px 0 10px 18px;font-size:13px;opacity:0.85;max-height:140px;overflow-y:auto">' +
        hostsPreview +
        (moreCount ? '<li style="opacity:0.6">…e mais ' + moreCount + '</li>' : '') +
      '</ul>' +
      '<label>Operador <span style="color:#f44336">*</span></label>' +
      '<input type="text" id="ra-operator" placeholder="roberto" required value="' + esc(localStorage.getItem('maint_operator') || '') + '">' +
      '<label>Motivo da liberação em massa <span style="color:#f44336">*</span></label>' +
      '<input type="text" id="ra-reason" placeholder="Ex.: fim da janela / reset emergencial" required>' +
      '<label style="display:flex;align-items:center;gap:8px;margin-top:10px;cursor:pointer">' +
        '<input type="checkbox" id="ra-confirm" style="width:auto;margin:0">' +
        '<span>Confirmo liberar os <strong>' + count + ' host(s)</strong> listados acima</span>' +
      '</label>' +
      '<label>OPS PIN <span style="color:#f44336">*</span></label>' +
      '<input type="password" id="ra-pin" placeholder="••••" required autocomplete="off">';

    const submitBtn = document.getElementById('maint-btn-submit');
    submitBtn.textContent = '🚨 Liberar ' + count;
    submitBtn.style.background = '#f44336';
    submitBtn.style.borderColor = '#f44336';

    const form = document.getElementById('maint-modal-form');
    form.onsubmit = async function (e) {
      e.preventDefault();
      await submitReleaseAll();
    };

    document.getElementById('maint-modal-overlay').classList.add('show');
    setTimeout(function () {
      document.getElementById('ra-operator').focus();
    }, 100);
  }

  async function submitReleaseAll() {
    const operator = document.getElementById('ra-operator').value.trim();
    const reason   = document.getElementById('ra-reason').value.trim();
    const confirm  = document.getElementById('ra-confirm').checked;
    const pin      = document.getElementById('ra-pin').value;

    if (!operator || !reason || !pin) return toast('Preencha todos os campos', 'error');
    if (!confirm) return toast('Marque a confirmação para prosseguir', 'error');

    const btn = document.getElementById('maint-btn-submit');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Liberando...';

    try {
      const r = await api('/api/maintenance/release_all?confirm=YES_RELEASE_ALL', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Ops-Pin': pin },
        body: JSON.stringify({ operator: operator, reason: reason, confirm: true }),
      });
      localStorage.setItem('maint_operator', operator);
      const released = (r && r.released_count != null)
        ? r.released_count
        : (r && Array.isArray(r.released) ? r.released.length : '?');
      toast('✅ ' + released + ' host(s) liberado(s)', 'success', 4500);
      closeModal();
      btn.style.background = '';
      btn.style.borderColor = '';
      await refresh();
    } catch (e) {
      toast('❌ ' + e.message, 'error', 6000);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }

  function ensureReleaseAllFAB() {
    if (document.getElementById('maint-fab-release')) return;

    // 🎨 Sprint 6g: estrutura HTML enriquecida com badge
    const btn = document.createElement('button');
    btn.id = 'maint-fab-release';
    btn.className = 'maint-fab-release';
    btn.setAttribute('aria-label', 'Liberar todos os hosts em manutenção');
    btn.innerHTML =
      '<span class="maint-fab-icon" aria-hidden="true">🚨</span>' +
      '<span class="maint-fab-badge" id="maint-fab-badge">0</span>';
    btn.addEventListener('click', openReleaseAllModal);
    document.body.appendChild(btn);

    // ⌨️ Atalho global Ctrl+Shift+M
    document.addEventListener('keydown', function (e) {
      if (e.ctrlKey && e.shiftKey && (e.key === 'M' || e.key === 'm')) {
        e.preventDefault();
        openReleaseAllModal();
      }
    });

    // 🔄 Estado local pra detectar mudanças (pra animar o badge)
    let _lastCount = 0;
    let _lastSeverity = '';

    function _calcSeverity(c) {
      if (c <= 0)  return '';
      if (c <= 3)  return 'low';
      if (c <= 7)  return 'mid';
      return 'high';
    }

    function _formatTooltip(hosts) {
      if (!hosts || hosts.length === 0) return '';
      const c = hosts.length;
      let avgMin = 0;
      const now = Date.now() / 1000;
      let count = 0;
      hosts.forEach(function (h) {
        const ts = Number(h.ts || h.timestamp || 0);
        if (ts > 0) {
          avgMin += (now - ts) / 60;
          count++;
        }
      });
      if (count > 0) avgMin = Math.round(avgMin / count);
      const tempo = avgMin > 60
        ? '~' + (avgMin / 60).toFixed(1) + 'h'
        : '~' + avgMin + 'min';
      return c + ' host' + (c > 1 ? 's' : '') + ' em manutenção • ' +
             tempo + ' em média • Ctrl+Shift+M';
    }

    function updateFabState() {
      const hosts = Array.isArray(_activeHosts) ? _activeHosts : [];
      const c = hosts.length;
      const severity = _calcSeverity(c);
      const badge = document.getElementById('maint-fab-badge');

      // 👁️ Visibilidade
      btn.style.display = c > 0 ? 'flex' : 'none';

      // 🎚️ Classes de severidade
      btn.classList.remove(
        'maint-fab-release--low',
        'maint-fab-release--mid',
        'maint-fab-release--high'
      );
      if (severity) btn.classList.add('maint-fab-release--' + severity);

      // 🔢 Badge: número + animação se mudou
      if (badge) {
        badge.textContent = c > 99 ? '99+' : String(c);
        if (c !== _lastCount && c > 0) {
          badge.classList.remove('maint-fab-badge--bump');
          // Force reflow pra reanimar
          void badge.offsetWidth;
          badge.classList.add('maint-fab-badge--bump');
        }
      }

      // 🛈 Tooltip rico
      btn.setAttribute('data-tip', _formatTooltip(hosts));
      btn.title = ''; // suprimi tooltip nativo (usamos CSS ::after)

      _lastCount = c;
      _lastSeverity = severity;
    }

    // 🔁 Polling (mesmo intervalo da versão anterior)
    setInterval(updateFabState, 1500);
    updateFabState(); // primeira chamada imediata
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensureReleaseAllFAB);
  } else {
    ensureReleaseAllFAB();
  }
  // ═══ SPRINT-6e-PATCH3:RELEASE_ALL:END ═══

  window.MaintUI = {
    refresh,
    openMarkModal,
    openClearModal,
    toast,
    getActive: () => Array.isArray(_activeHosts) ? _activeHosts.slice() : [],
    openReleaseAll: openReleaseAllModal,
  };

  // ── Init ──────────────────────────────────────────────────
  function init() {
    ensureFAB();
    ensureModal();
    refresh();
    if (_refreshTimer) clearInterval(_refreshTimer);
    _refreshTimer = setInterval(refresh, REFRESH_MS);
    console.log('[maint] UI inicializada ✅');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
