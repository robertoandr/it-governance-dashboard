// ═══════════════════════════════════════════════════════════════
// Dashboard de Governança de TI — Grupo Gadens
// ═══════════════════════════════════════════════════════════════

const CACHE_TTL = parseInt(document.body.dataset.cacheTtl || "60", 10);
const REFRESH_MS = CACHE_TTL * 1000;
let _lastData = null;
let _nextRefreshAt = Date.now() + REFRESH_MS;

// ── Tema ────────────────────────────────────────────────────────
function initTheme() {
  const saved = localStorage.getItem("dash-theme") || "dark";
  document.body.dataset.theme = saved;
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.textContent = saved === "dark" ? "◐" : "◑";
    btn.addEventListener("click", () => {
      const now = document.body.dataset.theme === "dark" ? "light" : "dark";
      document.body.dataset.theme = now;
      localStorage.setItem("dash-theme", now);
      btn.textContent = now === "dark" ? "◐" : "◑";
    });
  }
}

// ── Tabs ────────────────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll(".nav-item[data-tab]").forEach(btn => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.dataset.panel === target));
      const title = btn.querySelector("span:last-child")?.textContent || "Dashboard";
      const tbTitle = document.getElementById("tb-panel-title");
      if (tbTitle) tbTitle.textContent = title;
      // fecha sidebar no mobile
      document.getElementById("sidebar")?.classList.remove("open");
    });
  });
  document.getElementById("hamburger")?.addEventListener("click", () => {
    document.getElementById("sidebar")?.classList.toggle("open");
  });
}

// ── Helpers ─────────────────────────────────────────────────────
function classify(v, t) {
  if (v == null || isNaN(v) || !t) return null;
  if (v < t.critical) return "crit";
  if (v < t.warning) return "warn";
  return "ok";
}
function setKpiClass(key, cls) {
  const card = document.querySelector(`.kpi-card[data-key="${key}"]`);
  if (!card) return;
  card.classList.remove("crit", "warn", "ok");
  if (cls) card.classList.add(cls);
}
function pulseCard(key) {
  const card = document.querySelector(`.kpi-card[data-key="${key}"]`);
  if (!card) return;
  card.classList.remove("refreshed");
  void card.offsetWidth;
  card.classList.add("refreshed");
}
function setBar(id, pct, t) {
  const bar = document.getElementById(id);
  if (!bar) return;
  const p = Math.max(0, Math.min(100, pct || 0));
  bar.style.width = `${p}%`;
  bar.classList.remove("crit", "warn", "ok");
  const cls = classify(p, t);
  if (cls) bar.classList.add(cls);
}
function setText(id, value, fallback = "—") {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = (value === undefined || value === null || value === "") ? fallback : value;
}
function fmtClock(epochSec) {
  if (!epochSec) return "—";
  return new Date(epochSec * 1000).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
  });
}
function fmtRelative(iso) {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `há ${Math.floor(diff)}s`;
  if (diff < 3600) return `há ${Math.floor(diff/60)}min`;
  return new Date(iso).toLocaleString("pt-BR");
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  })[c]);
}
function statusBadge(text) {
  if (!text) return { cls: "warn", txt: "?" };
  const s = String(text).toLowerCase();
  if (s.includes("operational") || s.includes("normal") || s.includes("healthy")) return { cls: "ok", txt: text };
  if (s.includes("advisory") || s.includes("restor") || s.includes("investig")) return { cls: "warn", txt: text };
  return { cls: "crit", txt: text };
}

// ── Relógio e countdown ────────────────────────────────────────
function tickClock() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleTimeString("pt-BR");
  const cd = document.getElementById("countdown");
  if (cd) {
    const left = Math.max(0, Math.round((_nextRefreshAt - Date.now()) / 1000));
    cd.textContent = String(left).padStart(2, "0");
  }
}

// ── Ack / Unack ─────────────────────────────────────────────────
async function ackToggle(eventid, checkbox, isUnack) {
  if (!eventid) return;
  checkbox.disabled = true;
  const endpoint = isUnack ? "/api/unack" : "/api/ack";
  try {
    const r = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ eventid, message: "Atualizado via dashboard" }),
    });
    const data = await r.json();
    if (!data.ok) {
      alert("Falha: " + (data.error || "erro desconhecido"));
      checkbox.checked = !checkbox.checked;
      checkbox.disabled = false;
      return;
    }
    const tr = checkbox.closest("tr");
    if (tr) tr.classList.toggle("acked", !isUnack);
    setTimeout(refresh, 1500);
  } catch (e) {
    alert("Erro de rede: " + e.message);
    checkbox.checked = !checkbox.checked;
    checkbox.disabled = false;
  }
}
window.ackToggle = ackToggle;

// ═══════════════════════════════════════════════════════════════
// Render
// ═══════════════════════════════════════════════════════════════
function render(data) {
  const old = _lastData;
  _lastData = data;
  const T = data.thresholds || {};

  // ── Score de Saúde ─────────────────────────────────────────
  const hs = data.health_score || {};
  if (hs.score != null) updateScoreRing(hs.score);
  const scoreLabelEl = document.getElementById("score-label");
  if (scoreLabelEl) scoreLabelEl.textContent = hs.label || "—";
  const scComp = document.getElementById("score-components");
  if (scComp && Array.isArray(hs.components)) {
    scComp.innerHTML = hs.components.map(c => `
      <div class="score-comp">
        <div class="nm">${esc(c.name)} <span class="faint">(${c.weight}%)</span></div>
        <div class="vl">${c.value}${esc(c.unit)}</div>
      </div>`).join("");
  }

  // ── Visão Geral ────────────────────────────────────────────
  const ss = data.secure_score || {};
  if (ss.enabled !== false) {
    animateId("kpi-ss", ss.pct);
    animateId("kpi-ss-curr", ss.current);
    animateId("kpi-ss-max", ss.max);
    setBar("bar-ss", ss.pct, T.secure_score);
    setKpiClass("secure_score", classify(ss.pct, T.secure_score));
  }

  if (data.hosts) {
    animateId("kpi-hosts-pct", data.hosts.up_pct);
    animateId("kpi-hosts-up", data.hosts.up);
    animateId("kpi-hosts-down", data.hosts.down);
    animateId("kpi-hosts-total", data.hosts.total);
    setBar("bar-hosts", data.hosts.up_pct, T.hosts_up_pct);
    setKpiClass("hosts", classify(data.hosts.up_pct, T.hosts_up_pct));
    // aba Hosts
    setText("hosts-total", data.hosts.total);
    setText("hosts-up", data.hosts.up);
    setText("hosts-down", data.hosts.down);
    setText("hosts-pct", data.hosts.up_pct);
    setBar("bar-hosts2", data.hosts.up_pct, T.hosts_up_pct);
  }

  const trs = data.triggers || [];
  const trsCrit = trs.filter(t => t.severity_num >= 4).length;
  const trsAck = trs.filter(t => t.acknowledged).length;
  animateId("kpi-trg-total", trs.length);
  animateId("kpi-trg-crit", trsCrit);
  animateId("kpi-trg-ack", trsAck);
  setKpiClass("triggers", trsCrit > 0 ? "crit" : (trs.length > 0 ? "warn" : "ok"));
  // badge piscante no nav
  const navBadge = document.getElementById("nav-triggers-badge");
  if (navBadge) {
    const pending = trs.length - trsAck;
    if (pending > 0) {
      navBadge.textContent = pending;
      navBadge.style.display = "";
      navBadge.classList.toggle("pulse", trsCrit > 0);
    } else {
      navBadge.style.display = "none";
      navBadge.classList.remove("pulse");
    }
  }

  if (data.mfa) {
    animateId("kpi-mfa", data.mfa.pct);
    animateId("kpi-mfa-on", (data.mfa.habilitados ?? 0) + (data.mfa.enforced ?? 0));
    animateId("kpi-mfa-off", data.mfa.desabilitados);
    setBar("bar-mfa", data.mfa.pct, T.mfa);
    setKpiClass("mfa", classify(data.mfa.pct, T.mfa));
    // aba Segurança
    setText("sec-mfa", data.mfa.pct);
    setText("sec-mfa-on", (data.mfa.habilitados ?? 0) + (data.mfa.enforced ?? 0));
    setText("sec-mfa-off", data.mfa.desabilitados);
    setBar("bar-sec-mfa", data.mfa.pct, T.mfa);
    setKpiClass("security-mfa", classify(data.mfa.pct, T.mfa));
  }

  const it = data.intune || {};
  if (it.enabled !== false) {
    animateId("kpi-intune", it.pct);
    animateId("kpi-intune-c", it.compliant);
    animateId("kpi-intune-t", it.total);
    setBar("bar-intune", it.pct, T.intune);
    setKpiClass("intune", classify(it.pct, T.intune));
    setText("sec-intune", it.pct);
    setText("sec-intune-c", it.compliant);
    setText("sec-intune-t", it.total);
    setBar("bar-sec-intune", it.pct, T.intune);
    setKpiClass("security-intune", classify(it.pct, T.intune));
  }

  animateId("kpi-inc", (data.incidents || {}).total_ativos);

  if (data.users_m365) {
    animateId("kpi-users", data.users_m365.total);
    setText("kpi-users-sub", `${data.users_m365.ativos ?? 0} ativos · ${data.users_m365.inativos ?? 0} inativos`);
    setText("m365-users-total", data.users_m365.total);
    setText("m365-users-ativos", data.users_m365.ativos);
    setText("m365-users-inativos", data.users_m365.inativos);
  }

  // ── Segurança (Secure Score detalhado) ─────────────────────
  if (ss.enabled !== false) {
    setText("sec-ss", ss.pct);
    setText("sec-ss-curr", ss.current);
    setText("sec-ss-max", ss.max);
    setBar("bar-sec-ss", ss.pct, T.secure_score);
    setKpiClass("security-ss", classify(ss.pct, T.secure_score));
  }
  // Triggers de segurança = só M365
  const secTrigBody = document.getElementById("security-trig-body");
  if (secTrigBody) {
    const secTrigs = trs.filter(t => /M365|Microsoft|MFA|Score|Segurança/i.test(t.host + " " + t.description));
    if (!secTrigs.length) secTrigBody.innerHTML = '<tr><td colspan="4" class="muted center">Nenhum problema de segurança 🎉</td></tr>';
    else secTrigBody.innerHTML = secTrigs.map(t => `
      <tr class="${t.acknowledged ? "acked" : ""}">
        <td class="sev-cell ${t.severity}">${t.severity.toUpperCase().slice(0,4)}</td>
        <td>${esc(t.description)}</td>
        <td>${fmtClock(t.lastchange)}</td>
        <td><span class="badge-status ${t.acknowledged ? "ok" : "warn"}">${t.acknowledged ? "Reconhecido" : "Pendente"}</span></td>
      </tr>`).join("");
  }

  // ── AD ─────────────────────────────────────────────────────
  const ad = data.users_ad || {};
  if (ad.enabled === false) {
    setText("ad-users-total", "off");
    setText("ad-note", ad.note || "");
  } else {
    setText("ad-users-total", ad.total);
    setText("ad-users-active", ad.active);
    setText("ad-users-disabled", ad.disabled);
    setText("ad-note", ad.error ? `erro: ${ad.error}` : "");
  }

  // ── Service Health (M365) ──────────────────────────────────
  const sh = document.getElementById("service-health");
  if (sh && Array.isArray(data.service_health)) {
    if (!data.service_health.length) sh.innerHTML = '<li class="muted">Sem dados</li>';
    else sh.innerHTML = data.service_health.map(s => {
      const b = statusBadge(s.status_text);
      return `<li><span>${esc(s.service ?? "?")}</span><span class="badge-status ${b.cls}">${esc(b.txt)}</span></li>`;
    }).join("");
  }

  // ── Severidades + problemas ────────────────────────────────
  const bs = (data.problems && data.problems.by_severity) || {};
  setText("sev-disaster", bs.disaster ?? 0);
  setText("sev-high", bs.high ?? 0);
  setText("sev-average", bs.average ?? 0);
  setText("sev-warning", bs.warning ?? 0);
  setText("sev-info", bs.information ?? 0);

  const pb = document.getElementById("problems-body");
  const probItems = (data.problems && data.problems.items) || [];
  if (pb) {
    if (!probItems.length) pb.innerHTML = '<tr><td colspan="5" class="muted center">Nenhum problema 🎉</td></tr>';
    else pb.innerHTML = probItems.slice(0, 20).map(p => `
      <tr>
        <td class="sev-cell ${p.severity}">${p.severity.toUpperCase().slice(0,4)}</td>
        <td>${esc(p.host)}</td>
        <td>${esc(p.name)}</td>
        <td>${fmtClock(p.clock)}</td>
        <td>${p.acknowledged ? "✓" : "—"}</td>
      </tr>`).join("");
  }

  // ── Renderizadores dependentes de filtros ──────────────────
  renderTriggers();
  renderLicenses();
  renderFinops();
  renderM365Items();

  // ── Status pill + indicadores topo ─────────────────────────
  const sp = document.getElementById("status-pill");
  const st = document.getElementById("status-text");
  sp?.classList.remove("warn", "crit");
  if (data.last_error) {
    sp?.classList.add("warn");
    if (st) st.textContent = "Avisos";
  } else if (!data.last_refresh) {
    sp?.classList.add("crit");
    if (st) st.textContent = "Sem dados";
  } else {
    if (st) st.textContent = "Operacional";
  }

  // ── Animação de pulse nos cards com valor mudado ──────────
  if (old) {
    if (old.hosts?.up_pct !== data.hosts?.up_pct) pulseCard("hosts");
    if ((old.triggers || []).length !== trs.length) pulseCard("triggers");
    if ((old.incidents || {}).total_ativos !== (data.incidents || {}).total_ativos) pulseCard("incidents");
  }
}

// ═══════════════════════════════════════════════════════════════
// Renderizadores filtráveis
// ═══════════════════════════════════════════════════════════════
function renderTriggers() {
  const tbody = document.getElementById("triggers-body");
  if (!tbody || !_lastData) return;
  const f = (document.getElementById("trig-filter")?.value || "").toLowerCase();
  const hideAcked = document.getElementById("hide-acked")?.checked;
  const trs = (_lastData.triggers || []).filter(t => {
    if (hideAcked && t.acknowledged) return false;
    if (!f) return true;
    return (t.host + " " + t.description).toLowerCase().includes(f);
  });
  setText("trig-summary", `${(_lastData.triggers || []).length} totais · ${trs.length} mostradas`);
  if (!trs.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted center">Nenhuma trigger 🎉</td></tr>';
    return;
  }
  tbody.innerHTML = trs.map(t => `
    <tr class="${t.acknowledged ? "acked" : ""}">
      <td class="actions-cell">
        <input type="checkbox" ${t.acknowledged ? "checked" : ""}
               ${t.eventid ? "" : "disabled"}
               onchange="ackToggle('${esc(t.eventid || "")}', this, !this.checked)"
               title="${t.acknowledged ? "Reconhecido" : "Marcar resolvido"}">
      </td>
      <td class="sev-cell ${t.severity}">${t.severity.toUpperCase().slice(0,4)}</td>
      <td>${esc(t.host)}</td>
      <td>${esc(t.description)}</td>
      <td>${fmtClock(t.lastchange)}</td>
      <td><span class="badge-status ${t.acknowledged ? "ok" : "warn"}">${t.acknowledged ? "Reconhecido" : "Pendente"}</span></td>
    </tr>`).join("");
}

function renderLicenses() {
  const tbody = document.getElementById("licenses-body");
  if (!tbody || !_lastData) return;
  const f = (document.getElementById("lic-filter")?.value || "").toLowerCase();
  const lics = (_lastData.licenses || []).filter(l => !f || (l.name || "").toLowerCase().includes(f));
  setText("lic-summary", `${(_lastData.licenses || []).length} planos · ${lics.length} mostrados`);
  if (!lics.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted center">Sem licenças</td></tr>';
    return;
  }
  tbody.innerHTML = lics.map(l => `
    <tr>
      <td>${esc(l.name)}</td>
      <td>${l.consumed}</td>
      <td>${l.enabled}</td>
      <td>${l.free}</td>
      <td>${l.pct_used}%</td>
    </tr>`).join("");
}

function renderFinops() {
  if (!_lastData) return;
  const lics = _lastData.licenses || [];

  // Filtra: só pagas contam pra desperdício/custo
  const paid = lics.filter(l => !l.is_free);
  const free = lics.filter(l => l.is_free);

  // KPIs
  const totalMonthly = lics.reduce((a, l) => a + (l.monthly_total_brl || 0), 0);
  const totalAnnual = totalMonthly * 12;
  const totalWasteBrl = lics.reduce((a, l) => a + (l.waste_brl || 0), 0);
  const wasteUnits = paid.reduce((a, l) => a + (l.free || 0), 0);
  const overprov = paid.filter(l => (l.pct_used || 0) > 100).length;

  // Atualiza KPIs
  const fmt = v => "R$ " + v.toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  setText("finops-plans", `${lics.length}`);
  const plansSub = document.querySelector('.kpi-card[data-key="finops-total"] .kpi-sub');
  if (plansSub) plansSub.textContent = `${paid.length} pagas · ${free.length} gratuitas`;

  setText("finops-used", fmt(totalMonthly));
  const usedSub = document.querySelector('.kpi-card[data-key="finops-used"] .kpi-sub');
  if (usedSub) usedSub.textContent = `Anual: ${fmt(totalAnnual)}`;

  setText("finops-waste", fmt(totalWasteBrl));
  const wSub = document.querySelector('.kpi-card[data-key="finops-waste"] .kpi-sub');
  if (wSub) wSub.textContent = `${wasteUnits} licenças ociosas pagas`;
  setKpiClass("finops-waste", totalWasteBrl > 500 ? "crit" : (totalWasteBrl > 100 ? "warn" : "ok"));

  setText("finops-over", overprov);
  setKpiClass("finops-over", overprov > 0 ? "crit" : "ok");

  // Tabela: ordena por desperdício R$ desc (pagas com mais $$$ ocioso primeiro)
  const tbody = document.getElementById("finops-body");
  if (!tbody) return;
  const sorted = [...lics].sort((a, b) => {
    // Primeiro: pagas com desperdício
    const aw = a.is_free ? -1 : (a.waste_brl || 0);
    const bw = b.is_free ? -1 : (b.waste_brl || 0);
    return bw - aw;
  });
  if (!sorted.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted center">Sem dados</td></tr>';
    return;
  }
  tbody.innerHTML = sorted.slice(0, 15).map(l => {
    const freeIcon = l.is_free ? '<span style="color:var(--ok);font-weight:700" title="Plano gratuito">🆓</span>' : '<span class="muted">—</span>';
    const wasteCell = l.is_free
      ? '<span class="muted faint">—</span>'
      : `<span style="color:${(l.waste_brl||0) > 0 ? "var(--crit)" : "var(--ok)"}">${fmt(l.waste_brl || 0)}</span>`;
    return `<tr>
      <td>${esc(l.name)}</td>
      <td class="center">${freeIcon}</td>
      <td>${l.enabled}</td>
      <td>${l.consumed}</td>
      <td>${l.free}</td>
      <td>${l.is_free ? '<span class="muted">grátis</span>' : fmt(l.cost_brl || 0)}</td>
      <td>${wasteCell}</td>
    </tr>`;
  }).join("");
}

function renderM365Items() {
  const tbody = document.getElementById("m365-items-body");
  if (!tbody || !_lastData) return;
  const f = (document.getElementById("m365-filter")?.value || "").toLowerCase();
  const items = _lastData.m365_items || {};
  const keys = Object.keys(items).sort().filter(k =>
    !f || k.toLowerCase().includes(f) || (items[k].name || "").toLowerCase().includes(f)
  );
  setText("m365-items-count", Object.keys(items).length);
  if (!keys.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="muted center">Sem items</td></tr>';
    return;
  }
  tbody.innerHTML = keys.map(k => {
    const it = items[k];
    const v = it.value;
    const vStr = (typeof v === "number") ? v + (it.units ? " " + it.units : "") : esc(String(v));
    return `<tr>
      <td><code>${esc(k)}</code></td>
      <td>${esc(it.name || k)}</td>
      <td><b>${vStr}</b></td>
      <td class="muted small">${fmtClock(it.clock)}</td>
    </tr>`;
  }).join("");
}

// ═══════════════════════════════════════════════════════════════
// Refresh
// ═══════════════════════════════════════════════════════════════
async function refresh() {
  try {
    const r = await fetch("/api/data", { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    render(data);
    _nextRefreshAt = Date.now() + REFRESH_MS;
  } catch (e) {
    const sp = document.getElementById("status-pill");
    const st = document.getElementById("status-text");
    sp?.classList.remove("warn");
    sp?.classList.add("crit");
    if (st) st.textContent = "Erro";
    console.error("refresh:", e);
  }
}

// ═══════════════════════════════════════════════════════════════
// Bootstrap
// ═══════════════════════════════════════════════════════════════
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initTabs();
  document.getElementById("trig-filter")?.addEventListener("input", renderTriggers);
  document.getElementById("hide-acked")?.addEventListener("change", renderTriggers);
  document.getElementById("lic-filter")?.addEventListener("input", renderLicenses);
  document.getElementById("m365-filter")?.addEventListener("input", renderM365Items);
});

refresh();
setInterval(refresh, REFRESH_MS);
setInterval(tickClock, 1000);
tickClock();

// ═══════════════════════════════════════════════════════════════
// FINOPS — Edição inline com PIN
// ═══════════════════════════════════════════════════════════════
let _finopsPin = null;  // cache do PIN durante a sessão

function askPin() {
  return new Promise((resolve) => {
    const modal = document.getElementById("pin-modal");
    const input = document.getElementById("pin-input");
    const err = document.getElementById("pin-error");
    const ok = document.getElementById("pin-confirm");
    const cancel = document.getElementById("pin-cancel");
    if (!modal) { resolve(null); return; }
    err.style.display = "none";
    input.value = "";
    modal.style.display = "flex";
    setTimeout(() => input.focus(), 50);

    const cleanup = () => {
      modal.style.display = "none";
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      input.removeEventListener("keydown", onKey);
    };
    const onOk = () => {
      const v = input.value.trim();
      if (!/^\d{6}$/.test(v)) {
        err.textContent = "PIN deve ter exatamente 6 dígitos";
        err.style.display = "";
        return;
      }
      cleanup(); resolve(v);
    };
    const onCancel = () => { cleanup(); resolve(null); };
    const onKey = (e) => {
      if (e.key === "Enter") onOk();
      else if (e.key === "Escape") onCancel();
    };
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    input.addEventListener("keydown", onKey);
  });
}

async function ensurePin() {
  if (_finopsPin) return _finopsPin;
  const v = await askPin();
  if (v) _finopsPin = v;
  return v;
}

async function saveLicenseEdit(sku, field, value, tdEl) {
  const pin = await ensurePin();
  if (!pin) return false;
  try {
    const r = await fetch("/api/license-edit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({pin, sku, field, value}),
    });
    const data = await r.json();
    if (!data.ok) {
      if (r.status === 401) {
        _finopsPin = null;  // PIN errado, força nova entrada
        alert("PIN incorreto. Tente novamente.");
      } else {
        alert("Falha: " + (data.error || "erro"));
      }
      return false;
    }
    // Visual feedback
    if (tdEl) {
      tdEl.style.transition = "background 0.6s";
      tdEl.style.background = "rgba(34,197,94,0.25)";
      setTimeout(() => tdEl.style.background = "", 800);
    }
    setTimeout(refresh, 800);
    return true;
  } catch (e) {
    alert("Erro de rede: " + e.message);
    return false;
  }
}

function makeEditableCell(td, sku, field, currentValue) {
  td.addEventListener("click", function handler() {
    if (td.classList.contains("editing")) return;
    td.classList.add("editing");
    const orig = td.innerHTML;
    let input;
    if (field === "category") {
      input = document.createElement("select");
      ["paid", "free", "trial"].forEach(c => {
        const opt = document.createElement("option");
        opt.value = c; opt.textContent = c;
        if (c === currentValue) opt.selected = true;
        input.appendChild(opt);
      });
    } else {
      input = document.createElement("input");
      input.type = "number";
      input.step = "0.01";
      input.min = "0";
      input.value = currentValue;
    }
    td.innerHTML = "";
    td.appendChild(input);
    input.focus();
    if (input.select) input.select();

    const finish = async (save) => {
      td.classList.remove("editing");
      if (!save) { td.innerHTML = orig; return; }
      let val = input.value;
      if (field === "cost_brl") val = parseFloat(val);
      if (val === currentValue || (field === "cost_brl" && isNaN(val))) {
        td.innerHTML = orig;
        return;
      }
      const ok = await saveLicenseEdit(sku, field, val, td);
      if (!ok) td.innerHTML = orig;
      // se ok, refresh vai redesenhar
    };
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      else if (e.key === "Escape") finish(false);
    });
  });
}

// Sobrescreve renderFinops pra usar células editáveis + categoria + delta
function renderFinops() {
  if (!_lastData) return;
  const lics = _lastData.licenses || [];
  const paid  = lics.filter(l => l.category === "paid");
  const free  = lics.filter(l => l.category === "free");
  const trial = lics.filter(l => l.category === "trial");

  const totalMonthly = lics.reduce((a, l) => a + (l.monthly_total_brl || 0), 0);
  const totalAnnual = totalMonthly * 12;
  const totalWasteAnnual = lics.reduce((a, l) => a + (l.waste_brl || 0), 0) * 12;
  const wasteUnits = paid.reduce((a, l) => a + (l.free || 0), 0);
  const overprov = paid.filter(l => (l.pct_used || 0) > 100).length;

  const fmt = v => "R$ " + (v||0).toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  setText("finops-plans", lics.length);
  const plansSub = document.querySelector('.kpi-card[data-key="finops-total"] .kpi-sub');
  if (plansSub) plansSub.textContent = `${paid.length} pagas · ${free.length} grátis · ${trial.length} teste`;
  setText("finops-used", fmt(totalMonthly));
  const usedSub = document.querySelector('.kpi-card[data-key="finops-used"] .kpi-sub');
  if (usedSub) usedSub.textContent = `Anual: ${fmt(totalAnnual)}`;
  setText("finops-annual", fmt(totalAnnual));
  const annSub = document.querySelector('.kpi-card[data-key="finops-annual"] .kpi-sub');
  if (annSub) annSub.textContent = `${fmt(totalMonthly)} × 12 meses`;
  setText("finops-waste", fmt(totalWasteAnnual));
  const wSub = document.querySelector('.kpi-card[data-key="finops-waste"] .kpi-sub');
  if (wSub) wSub.textContent = `${wasteUnits} pagas ociosas (anual)`;
  setKpiClass("finops-waste", totalWasteAnnual > 6000 ? "crit" : (totalWasteAnnual > 1200 ? "warn" : "ok"));
  setText("finops-over", overprov);
  setKpiClass("finops-over", overprov > 0 ? "crit" : "ok");

  const tbody = document.getElementById("finops-body");
  if (!tbody) return;
  const sorted = [...lics].sort((a, b) => {
    const aw = a.category === "paid" ? (a.waste_brl || 0) : -1;
    const bw = b.category === "paid" ? (b.waste_brl || 0) : -1;
    return bw - aw;
  });
  if (!sorted.length) { tbody.innerHTML = '<tr><td colspan="9" class="muted center">Sem dados</td></tr>'; return; }

  tbody.innerHTML = "";
  sorted.slice(0, 15).forEach(l => {
    const tr = document.createElement("tr");
    const catLabel = l.category === "free" ? "Gratuito" : l.category === "paid" ? "Pago" : l.category === "trial" ? "Teste" : l.category;
    const catBadge = `<span class="cat-badge ${l.category}">${catLabel}</span>`;
    let deltaCell = '<span class="delta-zero">—</span>';
    if (l.delta_pct != null && l.previous_cost_brl != null) {
      const sign = l.delta_pct > 0 ? "+" : "";
      const cls = l.delta_pct > 0 ? "delta-up" : (l.delta_pct < 0 ? "delta-down" : "delta-zero");
      deltaCell = `<span class="${cls}" title="anterior mensal: ${fmt(l.previous_cost_brl)}">${sign}${l.delta_abs.toFixed(2)} (${sign}${l.delta_pct}%)</span>`;
    }
    const annualWaste = (l.waste_brl || 0) * 12;
    const wasteCell = (l.category === "paid")
      ? `<span style="color:${annualWaste > 0 ? "var(--crit)" : "var(--ok)"}">${fmt(annualWaste)}</span>`
      : '<span class="muted faint">—</span>';
    const costMonthly = l.category === "free" ? '<span class="muted">grátis</span>' : fmt(l.cost_brl || 0);
    const costAnnual = l.category === "free" ? '<span class="muted">—</span>' : fmt((l.cost_brl || 0) * (l.enabled || 0) * 12);
    tr.innerHTML = `
      <td>${esc(l.name)}</td>
      <td class="editable-cat" data-sku="${esc(l.sku)}">${catBadge}</td>
      <td>${l.enabled}</td>
      <td>${l.consumed}</td>
      <td>${l.free}</td>
      <td class="editable-cost" data-sku="${esc(l.sku)}">${costMonthly}</td>
      <td class="muted">${costAnnual}</td>
      <td>${deltaCell}</td>
      <td>${wasteCell}</td>
    `;
    tbody.appendChild(tr);
    const catCell = tr.querySelector(".editable-cat");
    const costCell = tr.querySelector(".editable-cost");
    if (catCell) { catCell.classList.add("editable"); makeEditableCell(catCell, l.sku, "category", l.category); }
    if (costCell) { costCell.classList.add("editable"); makeEditableCell(costCell, l.sku, "cost_brl", l.cost_brl || 0); }
  });
}

// ═══════════════════════════════════════════════════════════════
// CFTV — Vigilância Operacional (read-only, decisão manual no Zabbix)
// ═══════════════════════════════════════════════════════════════
const ZABBIX_FRONT_URL = document.body.dataset.zabbixUrl || "";

function zbxHostLink(hostid, label) {
  const safeLabel = esc(label || "—");
  if (!ZABBIX_FRONT_URL || !hostid) return safeLabel;
  const url = `${ZABBIX_FRONT_URL}/zabbix.php?action=host.view&filter_hostids%5B%5D=${encodeURIComponent(hostid)}&filter_set=1`;
  return `<a href="${url}" target="_blank" rel="noopener noreferrer"
            style="color:var(--accent);text-decoration:none;font-weight:600;">
            🔗 Abrir no Zabbix
          </a>`;
}

function renderCFTV(cftv) {
  if (!cftv) return;

  const total = cftv.total || 0;
  const up    = cftv.up    || 0;
  const down  = cftv.down  || 0;
  const maint = cftv.maint || 0;
  const pct   = total > 0 ? (up / total) * 100 : 0;

  // ── KPIs ───────────────────────────────────────────────────
  setText("cftv-total", total);
  setText("cftv-up",    up);
  setText("cftv-down",  down);
  setText("cftv-maint", maint);

  // ── Gauge SVG (circumference = 2*π*52 ≈ 326.7) ─────────────
  const CIRC = 326.7;
  const offset = CIRC - (CIRC * pct / 100);
  const bar = document.getElementById("cftv-gauge-bar");
  const pctEl = document.getElementById("cftv-gauge-pct");
  if (bar) {
    bar.style.strokeDashoffset = offset;
    let color = "#10b981";              // verde
    if (pct < 98) color = "#f59e0b";    // amarelo
    if (pct < 90) color = "#ef4444";    // vermelho
    bar.style.stroke = color;
  }
  if (pctEl) pctEl.textContent = total > 0 ? pct.toFixed(1) + "%" : "—";

  // ── Banner inteligente ─────────────────────────────────────
  const banner = document.getElementById("cftv-banner");
  const bannerText = document.getElementById("cftv-banner-text");
  if (banner && bannerText) {
    if (down > 0) {
      banner.classList.add("show");
      banner.style.background = "rgba(239,68,68,0.15)";
      banner.style.borderLeft = "4px solid #ef4444";
      bannerText.textContent = `⚠️ Atenção: ${down} dispositivo(s) offline detectado(s)`;
    } else if (maint > 0) {
      banner.classList.add("show");
      banner.style.background = "rgba(245,158,11,0.15)";
      banner.style.borderLeft = "4px solid #f59e0b";
      bannerText.textContent = `🔧 ${maint} dispositivo(s) em manutenção planejada`;
    } else {
      banner.classList.remove("show");
    }
  }

  // ── Categorias (camera, facial, dvr, antena, tela) ─────────
  const cats = cftv.categories || {};
  ["camera", "facial", "dvr", "antena", "tela"].forEach(key => {
    const c = cats[key] || { total: 0, up: 0, down: 0, maint: 0 };
    const cTotal = c.total || 0;
    const cUp    = c.up    || 0;
    const cPct   = cTotal > 0 ? (cUp / cTotal) * 100 : 0;

    const statsEl = document.getElementById(`cftv-cat-${key}`);
    if (statsEl) {
      statsEl.innerHTML = cTotal === 0
        ? `<span style="color:var(--text-mute)">sem itens</span>`
        : `<span style="color:var(--success)">${cUp}</span> / ${cTotal}
           ${c.down ? `· <span style="color:var(--danger)">${c.down} off</span>` : ""}
           ${c.maint ? `· <span style="color:var(--warn)">${c.maint} mnt</span>` : ""}`;
    }
    const barEl = document.getElementById(`cftv-bar-${key}`);
    if (barEl) {
      barEl.style.width = cPct + "%";
      let color = "#10b981";
      if (cPct < 98) color = "#f59e0b";
      if (cPct < 90) color = "#ef4444";
      barEl.style.background = color;
      barEl.style.height = "6px";
      barEl.style.borderRadius = "3px";
      barEl.style.transition = "width .6s ease, background .3s";
    }
  });

  // ── Tabela DOWN ────────────────────────────────────────────
  const downList = document.getElementById("cftv-down-list");
  if (downList) {
    const items = cftv.down_items || [];
    if (items.length === 0) {
      downList.innerHTML = `
        <tr><td colspan="4" style="padding:14px;text-align:center;color:var(--success);">
          ✅ Nenhum dispositivo offline
        </td></tr>`;
    } else {
      downList.innerHTML = items.map(it => `
        <tr style="border-bottom:1px solid var(--border);">
          <td style="padding:10px;font-weight:600;">${esc(it.name || "—")}</td>
          <td style="padding:10px;font-family:monospace;color:var(--text-mute);">${esc(it.ip || "—")}</td>
          <td style="padding:10px;">${esc(it.location || "—")}</td>
          <td style="padding:10px;text-align:right;">${zbxHostLink(it.hostid, it.name)}</td>
        </tr>
      `).join("");
    }
  }

  // ── Tabela MAINT (read-only — decisão manual no Zabbix) ────
  const maintList = document.getElementById("cftv-maint-list");
  if (maintList) {
    const items = cftv.maint_items || [];
    if (items.length === 0) {
      maintList.innerHTML = `
        <tr><td colspan="5" style="padding:14px;text-align:center;color:var(--text-mute);">
          Nenhum dispositivo em manutenção
        </td></tr>`;
    } else {
      maintList.innerHTML = items.map(it => `
        <tr style="border-bottom:1px solid var(--border);">
          <td style="padding:10px;font-weight:600;">${esc(it.name || "—")}</td>
          <td style="padding:10px;font-family:monospace;color:var(--text-mute);">${esc(it.ip || "—")}</td>
          <td style="padding:10px;">${esc(it.location || "—")}</td>
          <td style="padding:10px;"><span style="padding:2px 8px;border-radius:10px;background:rgba(245,158,11,0.15);color:var(--warn);font-size:11px;">${esc(it.category || "—")}</span></td>
          <td style="padding:10px;text-align:right;">${zbxHostLink(it.hostid, it.name)}</td>
        </tr>
      `).join("");
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// GOVERNANÇA DE TI 360° — funções de animação (append-only)
// ═══════════════════════════════════════════════════════════════

function animateCounter(el, target, duration) {
  duration = duration || 800;
  const start = parseFloat(el.textContent) || 0;
  const end   = parseFloat(target);
  if (isNaN(end)) { el.textContent = target; return; }
  const startTime = performance.now();
  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const ease = 1 - Math.pow(1 - progress, 3); // easeOutCubic
    el.textContent = Math.round(start + (end - start) * ease);
    if (progress < 1) requestAnimationFrame(step);
    else el.textContent = Number.isInteger(end) ? end : target;
  }
  requestAnimationFrame(step);
}

function animateId(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  const num = parseFloat(value);
  if (!isNaN(num)) animateCounter(el, num);
  else el.textContent = value ?? "—";
}

function updateScoreRing(score) {
  const circumference = 314.16; // 2 * π * 50
  const fill   = document.getElementById("score-ring-fill");
  const valEl  = document.getElementById("score-value");
  const card   = document.getElementById("score-card-wrap");
  if (!fill || !valEl) return;

  const pct    = Math.max(0, Math.min(100, score));
  const offset = circumference - (pct / 100) * circumference;
  fill.style.strokeDashoffset = offset;

  // Cor dinâmica
  fill.classList.remove("is-critical", "is-warning", "is-ok");
  if (pct >= 85)      fill.classList.add("is-ok");
  else if (pct >= 60) fill.classList.add("is-warning");
  else                fill.classList.add("is-critical");

  // Sincroniza classe no card wrapper para glow
  if (card) {
    card.classList.remove("is-ok", "is-warning", "is-critical");
    if (pct >= 85)      card.classList.add("is-ok");
    else if (pct >= 60) card.classList.add("is-warning");
    else                card.classList.add("is-critical");
  }

  animateCounter(valEl, pct);
}

// Inicia anel em 0 assim que o DOM carrega (antes do primeiro fetch)
document.addEventListener("DOMContentLoaded", function () {
  const fill = document.getElementById("score-ring-fill");
  if (fill) fill.style.strokeDashoffset = "314.16";
});
