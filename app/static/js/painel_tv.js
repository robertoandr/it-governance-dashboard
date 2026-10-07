/* Painel de TV do NOC — base comum às telas /gov/v1 … /gov/v5.
 *
 * Cada tela define window.PainelTV.render(dados) → HTML do palco 1920×1080.
 * Este arquivo cuida do resto: escala o palco para a tela, renderiza com os
 * dados embutidos na página, busca dados novos a cada 60 s e, se a busca
 * falhar, mantém o último dado na tela com o horário dele destacado.
 * Todo texto vindo das fontes passa por esc() antes de virar HTML.
 */
(function () {
  "use strict";

  const ICONES = {
    ok: '<circle cx="12" cy="12" r="9.5"></circle><path d="M7.5 12.5l3 3 6-6"></path>',
    warn: '<path d="M12 3l10 18H2z"></path><path d="M12 10v5"></path><path d="M12 18h.01"></path>',
    crit: '<path d="M8 2.5h8l5.5 5.5v8L16 21.5H8L2.5 16V8z"></path><path d="M9 9l6 6M15 9l-6 6"></path>',
    none: '<circle cx="12" cy="12" r="9.5"></circle><path d="M8 12h8"></path>',
    pulso: '<path d="M3 12h4l3-8 4 16 3-8h4"></path>',
    servidor: '<rect x="3" y="4" width="18" height="7" rx="1.5"></rect><rect x="3" y="13" width="18" height="7" rx="1.5"></rect><path d="M7 7.5h.01M7 16.5h.01"></path>',
    escudo: '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"></path><path d="M9 12l2 2 4-4"></path>',
    chamado: '<path d="M4 5h16v11H8l-4 4z"></path><path d="M8 9.5h8M8 12.5h5"></path>',
    sino: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"></path><path d="M10 20a2 2 0 0 0 4 0"></path>',
    predio: '<path d="M4 21V5l8-2v18M12 21h8V9l-8-2"></path><path d="M7 8h2M7 12h2M7 16h2M15 12h2M15 16h2"></path>',
    link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"></path><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"></path>',
    licenca: '<rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M7 10h6M7 14h4"></path><circle cx="17" cy="12" r="2"></circle>',
    notebook: '<rect x="4" y="5" width="16" height="11" rx="1.5"></rect><path d="M2 19h20"></path>',
  };

  function icone(nome, tam, extra) {
    return '<svg class="ic" width="' + (tam || 32) + '" height="' + (tam || 32) +
      '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"' +
      (extra || "") + ">" + (ICONES[nome] || ICONES.none) + "</svg>";
  }

  function esc(v) {
    return String(v === null || v === undefined ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /** Número no formato brasileiro; "—" quando não há dado. */
  function num(v, casas) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas || 0, maximumFractionDigits: casas || 0 });
  }

  /** Faixa de um percentual/score: ok > 85, warn 60–85, crit < 60. */
  function faixa(v) {
    if (v === null || v === undefined) return "none";
    return v > 85 ? "ok" : v >= 60 ? "warn" : "crit";
  }

  /** Faixa de uso de um recurso que esgota (licenças): crit ≥ 95%, warn ≥ 85%, ok abaixo. */
  function faixaUso(v) {
    if (v === null || v === undefined) return "none";
    return v >= 95 ? "crit" : v >= 85 ? "warn" : "ok";
  }

  const TEXTO = { ok: "Operacional", warn: "Atenção", crit: "Crítico", none: "Sem dados" };

  /** Tempo desde um ISO: "17 min", "1 h 05", "6 d 21 h". */
  function desde(iso) {
    if (!iso) return "—";
    const min = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
    if (min < 60) return min + " min";
    const h = Math.floor(min / 60);
    if (h < 24) return h + " h " + String(min % 60).padStart(2, "0");
    return Math.floor(h / 24) + " d " + (h % 24) + " h";
  }

  function horas(iso) {
    return iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 3600000) : 0;
  }

  const palco = document.getElementById("palco");
  const URL_DADOS = palco.dataset.url;
  let dados = JSON.parse(document.getElementById("dados-painel").textContent);
  let falhou = false;

  function escala() {
    const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    palco.style.transform = "translate(-50%, -50%) scale(" + s + ")";
  }

  function desenhar() {
    try {
      palco.innerHTML = window.PainelTV.render(dados);
    } catch (e) {
      // Uma tela que quebra não pode deixar a TV em branco: mostra o aviso.
      palco.innerHTML = '<div class="tv-erro">Não foi possível montar esta tela. Último dado: ' + esc(dados.hora) + "</div>";
    }
    const selo = palco.querySelector("[data-atualizacao]");
    if (selo) {
      selo.textContent = falhou ? "sem conexão · último dado " + dados.hora : "atualizado às " + dados.hora;
      selo.classList.toggle("tv-velho", falhou);
    }
    if (window.PainelTV && window.PainelTV.depois) window.PainelTV.depois(dados);
  }

  async function atualizar() {
    try {
      const r = await fetch(URL_DADOS, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error("HTTP " + r.status);
      dados = await r.json();
      falhou = false;
    } catch (e) {
      falhou = true;
    }
    desenhar();
  }

  window.TV = { icone: icone, esc: esc, num: num, faixa: faixa, faixaUso: faixaUso, TEXTO: TEXTO, desde: desde, horas: horas };

  window.addEventListener("resize", escala);
  document.addEventListener("DOMContentLoaded", function () {
    escala();
    desenhar();
    setInterval(atualizar, 60000);
  });
})();
