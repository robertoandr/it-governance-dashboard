(function () {
  "use strict";

  const currentPath = window.location.pathname;

  function renderSidebar(data) {
    const nav = document.getElementById("sidebar-nav");
    if (!nav) return;

    const domains = data.domains || [];
    const active = domains.filter(d => d.status === "active");
    const planned = domains.filter(d => d.status === "planned");

    let html = "";

    const homeActive = (currentPath === "/v2" || currentPath === "/v2/") ? "active" : "";
    html += `
      <a href="/v2" class="nav-item ${homeActive}">
        <span class="nav-icon">🏠</span>
        <span class="nav-label">Visão Geral</span>
      </a>
    `;

    if (active.length) {
      html += `<div class="nav-group-label">Ativos</div>`;
      active.forEach(d => {
        const isActive = currentPath.startsWith(d.route) ? "active" : "";
        const sla = d.sla || {};
        const badge = sla.current != null
          ? `<span class="nav-badge">${sla.current.toFixed(1)}%</span>`
          : "";
        html += `
          <a href="${d.route}" class="nav-item ${isActive}">
            <span class="nav-icon">${d.icon}</span>
            <span class="nav-label">${d.name}</span>
            ${badge}
          </a>
        `;
      });
    }

    if (planned.length) {
      html += `<div class="nav-group-label">Em Breve</div>`;
      planned.forEach(d => {
        html += `
          <div class="nav-item planned" title="Disponível em ${d.eta || 'breve'}">
            <span class="nav-icon">${d.icon}</span>
            <span class="nav-label">${d.name}</span>
            <span class="nav-badge">🔒 ${d.eta || "—"}</span>
          </div>
        `;
      });
    }

    nav.innerHTML = html;
  }

  function updateOrgBranding(org) {
    if (!org) return;
    const brandStrong = document.querySelector(".brand-text strong");
    const brandSmall = document.querySelector(".brand-text small");
    if (brandStrong && org.short_name) brandStrong.textContent = org.short_name;
    if (brandSmall && org.dashboard_title) brandSmall.textContent = org.dashboard_title;
  }

  function updateLastRefresh() {
    const el = document.getElementById("last-refresh");
    if (!el) return;
    const now = new Date();
    el.textContent = "Atualizado " + now.toLocaleTimeString("pt-BR");
  }

  function setupSidebarToggle() {
    const btn = document.getElementById("sidebar-toggle");
    const sidebar = document.getElementById("sidebar");
    if (btn && sidebar) {
      btn.addEventListener("click", () => sidebar.classList.toggle("open"));
    }
  }

  function setupCtrlK() {
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        alert("🔍 Busca global - em breve!");
      }
    });
  }

  async function init() {
    try {
      const resp = await fetch("/api/governance/owners");
      const data = await resp.json();
      renderSidebar(data);
      updateOrgBranding(data.organization);
      updateLastRefresh();
    } catch (err) {
      console.error("Erro ao carregar governance:", err);
      const nav = document.getElementById("sidebar-nav");
      if (nav) nav.innerHTML = `<div class="nav-loading">❌ Erro ao carregar</div>`;
    }
    setupSidebarToggle();
    setupCtrlK();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
