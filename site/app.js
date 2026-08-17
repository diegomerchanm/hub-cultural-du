/* Hub Cultural — app.js
   Sitio estático: lee data.json (generado por 5_export_dashboard_data.py),
   calcula el ranking final en el navegador (los sub-scores qScore/aScore/
   bScore ya vienen precalculados desde el export; acá se agregan T
   -proximidad temporal, depende de "hoy"- y C -contexto de sesión, depende
   de localStorage de cada visitante- ver docs/dashboard_redesign_proposal.md
   sección 3). Sin backend, sin dependencia de Neo4j en producción. */

const CATEGORY_META = {
  gastronomico:  { label: "Gastronomía",            color: "#b0384a", icon: "ti-tools-kitchen-2" },
  institucional: { label: "Institucional",           color: "#2f5aa8", icon: "ti-building-bank" },
  visual:        { label: "Artes visuales",          color: "#7b5ea7", icon: "ti-palette" },
  comunitario:   { label: "Comunidad",               color: "#4a8c6f", icon: "ti-users" },
  musical:       { label: "Música",                  color: "#a5603a", icon: "ti-music" },
  formacion:     { label: "Talleres",                color: "#c49a2c", icon: "ti-school" },
  audiovisual:   { label: "Cine",                    color: "#5f6b8c", icon: "ti-movie" },
  escenico:      { label: "Teatro y danza",          color: "#8f8d84", icon: "ti-masks-theater" },
  festival:      { label: "Festivales",              color: "#e0b02e", icon: "ti-confetti" },
  academico:     { label: "Charlas y conferencias",  color: "#6b8c4a", icon: "ti-microphone-2" },
  politico:      { label: "Cívico",                  color: "#8f8d84", icon: "ti-ballot" },
};
const TAG_ICONS = {
  "Literatura": "ti-book", "Circo": "ti-balloon", "Fotografía": "ti-camera",
  "Moda": "ti-shirt", "Arquitectura": "ti-building", "Cómic": "ti-book-2",
  "Danza": "ti-music", "Teatro": "ti-masks-theater", "Cine": "ti-movie",
  "Música": "ti-music", "Artes visuales": "ti-palette", "Gastronomía": "ti-tools-kitchen-2",
};
const FALLBACK_TAG = { color: "#6b6a63", icon: "ti-star" };

const GEO_LABEL = { "Île-de-France": "Île-de-France", "Francia fuera IDF": "Francia (fuera IDF)", "Fuera de Francia": "Fuera de Francia" };

const PREFS_KEY = "hcdu_prefs";

let DATA = { events: [], accounts: [] };
let ACCOUNTS_BY_USER = {};
let STATE = { geo: "all", when: "upcoming", theme: "all", free: false, sort: "recommended" };

/* ── Preferencias de sesión (sin login, ver sección 3.5 de la propuesta) ── */
function loadPrefs() {
  let prefs;
  try { prefs = JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch (e) { prefs = {}; }
  prefs.catWeights = prefs.catWeights || {};
  prefs.zone = prefs.zone || null;
  const last = prefs.updatedAt ? new Date(prefs.updatedAt) : null;
  if (last) {
    const days = Math.max(0, Math.floor((Date.now() - last.getTime()) / 86400000));
    const decay = Math.pow(0.8, days);
    for (const k of Object.keys(prefs.catWeights)) {
      prefs.catWeights[k] *= decay;
      if (prefs.catWeights[k] < 0.5) delete prefs.catWeights[k];
    }
  }
  return prefs;
}
function savePrefs(prefs) {
  prefs.updatedAt = new Date().toISOString();
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) {}
}
function bumpPref(theme, geoZone, weight) {
  const prefs = loadPrefs();
  if (theme) prefs.catWeights[theme] = (prefs.catWeights[theme] || 0) + weight;
  if (geoZone) prefs.zone = geoZone;
  savePrefs(prefs);
}

/* ── Temas de un evento: category (confiable, por evento) + eventArtTags
   (rico, DD-042) — unión, sin duplicar. El menú se arma dinámicamente a
   partir de esto, no hay lista fija (decisión 2026-08-14). ────────────── */
function eventThemes(ev) {
  const themes = new Set();
  const catMeta = CATEGORY_META[ev.category];
  if (catMeta) themes.add(catMeta.label);
  (ev.eventArtTags || []).forEach((tag) => themes.add(tag));
  return themes;
}
function themeMeta(theme) {
  const cat = Object.values(CATEGORY_META).find((c) => c.label === theme);
  if (cat) return cat;
  return { color: FALLBACK_TAG.color, icon: TAG_ICONS[theme] || FALLBACK_TAG.icon };
}

/* ── Fecha / proximidad temporal (T) — depende de "hoy", nunca precalculado */
function daysUntil(dateStr) {
  if (!dateStr) return null;
  const d = new Date(dateStr + "T00:00:00");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((d - today) / 86400000);
}
function computeT(dateStr) {
  const d = daysUntil(dateStr);
  if (d === null || d < 0) return 0;
  if (d <= 2) return 1.0;
  if (d <= 7) return 0.85;
  if (d <= 21) return 0.60;
  if (d <= 60) return 0.35;
  return 0.15;
}
function whenBucket(dateStr) {
  const d = daysUntil(dateStr);
  if (d === null) return null;
  if (d === 0) return "today";
  if (d >= 0 && d <= 6) return "week";
  return d >= 0 ? "later" : "past";
}

/* ── C: contexto de sesión (catMatch + geoMatch), 0.5 neutro sin señal ── */
function computeC(ev, prefs) {
  const weights = prefs.catWeights || {};
  const keys = Object.keys(weights);
  let catMatch = 0;
  if (keys.length) {
    const max = Math.max(...Object.values(weights));
    const themes = eventThemes(ev);
    let best = 0;
    themes.forEach((th) => { if (weights[th]) best = Math.max(best, weights[th] / max); });
    catMatch = best;
  }
  let geoMatch = 0;
  if (prefs.zone) geoMatch = ev.geoZone === prefs.zone ? 1 : 0.5;
  if (!keys.length && !prefs.zone) return 0.5;
  return 0.6 * catMatch + 0.4 * geoMatch;
}

function relevance(ev, prefs) {
  const T = computeT(ev.eventDate);
  const C = computeC(ev, prefs);
  const q = ev.qScore || 0, a = ev.aScore || 0, b = ev.bScore || 0, p = ev.penaltyMultiplier ?? 1;
  return 100 * (0.30 * q + 0.22 * a + 0.18 * b + 0.20 * T + 0.10 * C) * p;
}

/* ── Formato ─────────────────────────────────────────────────────────── */
const WEEKDAY_ES = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const MONTH_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
function fmtDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr + "T00:00:00");
  if (isNaN(d)) return dateStr;
  return `${d.getDate()} ${MONTH_ES[d.getMonth()]}`;
}

/* ── Filtrado ────────────────────────────────────────────────────────── */
// DD-045 (punto 4): "por venir" y "pasados" son dos universos separados.
// isUpcoming() es la misma regla que ya usaba la rama "upcoming" — se
// extrae acá porque ahora también la necesitan renderFilterBar() (para que
// los contadores de las pills reflejen solo eventos por venir) y el bucket
// "past" nuevo.
function isUpcoming(ev) {
  const d = daysUntil(ev.eventDate);
  return d !== null && d >= 0;
}
function applyFilters(events) {
  return events.filter((ev) => {
    if (STATE.geo !== "all" && ev.geoZone !== STATE.geo) return false;
    if (STATE.when === "past") {
      if (isUpcoming(ev)) return false;
      // "Pasados" es un bucket sin clasificar por tema a propósito
      // (decisión de producto 2026-08-15, DD-045) — se salta el filtro de
      // tema más abajo con la condición STATE.when !== "past".
    } else if (STATE.when !== "upcoming") {
      const bucket = whenBucket(ev.eventDate);
      if (bucket === "past" || bucket === null) return false;
      if (STATE.when === "today" && bucket !== "today") return false;
      if (STATE.when === "week" && !(bucket === "today" || bucket === "week")) return false;
      if (STATE.when === "month") {
        const d = daysUntil(ev.eventDate);
        if (d === null || d < 0 || d > 31) return false;
      }
    } else {
      if (!isUpcoming(ev)) return false;
    }
    if (STATE.when !== "past" && STATE.theme !== "all" && !eventThemes(ev).has(STATE.theme)) return false;
    if (STATE.free && !ev.isFree) return false;
    return true;
  });
}

function diversify(sorted, maxPerAuthor, maxPerTheme, limit) {
  const authorCount = {}, themeCount = {}, out = [];
  for (const ev of sorted) {
    if (out.length >= limit) break;
    const author = ev.sourceAuthor || "?";
    if ((authorCount[author] || 0) >= maxPerAuthor) continue;
    const themes = [...eventThemes(ev)];
    const primaryTheme = themes[0] || "?";
    if ((themeCount[primaryTheme] || 0) >= maxPerTheme) continue;
    authorCount[author] = (authorCount[author] || 0) + 1;
    themeCount[primaryTheme] = (themeCount[primaryTheme] || 0) + 1;
    out.push(ev);
  }
  return out;
}

/* ── Render: filtros dinámicos ──────────────────────────────────────── */
function renderFilterBar() {
  // DD-045 (punto 4): los contadores de zona/tema reflejan solo eventos por
  // venir — antes contaban DATA.events completo (pasados incluidos), por
  // eso una categoría podía mostrar "Cine: 6" con los 6 ya pasados.
  const upcomingEvents = DATA.events.filter(isUpcoming);

  const zoneCounts = {};
  upcomingEvents.forEach((ev) => { if (ev.geoZone) zoneCounts[ev.geoZone] = (zoneCounts[ev.geoZone] || 0) + 1; });
  const geoEl = document.getElementById("geo-pills");
  geoEl.innerHTML = "";
  geoEl.appendChild(pillEl(t("geoAll"), upcomingEvents.length, STATE.geo === "all", () => setState({ geo: "all" })));
  Object.keys(zoneCounts).sort((a, b) => zoneCounts[b] - zoneCounts[a]).forEach((zone) => {
    geoEl.appendChild(pillEl(GEO_LABEL[zone] || zone, zoneCounts[zone], STATE.geo === zone, () => setState({ geo: zone })));
  });

  // Pills de fecha, cada una con su propio conteo (independiente entre sí,
  // sobre el dataset completo) — "Pasados" es el complemento de "por venir".
  const whenEl = document.getElementById("when-pills");
  whenEl.innerHTML = "";
  const todayN = DATA.events.filter((ev) => whenBucket(ev.eventDate) === "today").length;
  const weekN = DATA.events.filter((ev) => { const b = whenBucket(ev.eventDate); return b === "today" || b === "week"; }).length;
  const monthN = DATA.events.filter((ev) => { const d = daysUntil(ev.eventDate); return d !== null && d >= 0 && d <= 31; }).length;
  const upcomingN = upcomingEvents.length;
  const pastN = DATA.events.length - upcomingN;
  [
    ["today", t("whenToday"), todayN],
    ["week", t("whenWeek"), weekN],
    ["month", t("whenMonth"), monthN],
    ["upcoming", t("whenUpcoming"), upcomingN],
    ["past", t("whenPast"), pastN],
  ].forEach(([key, label, n]) => whenEl.appendChild(pillEl(label, n, STATE.when === key, () => setState({ when: key }))));

  const freeBtn = document.getElementById("free-toggle");
  freeBtn.classList.toggle("active", STATE.free);
  freeBtn.onclick = () => setState({ free: !STATE.free });

  const themeCounts = {};
  upcomingEvents.forEach((ev) => eventThemes(ev).forEach((th) => { themeCounts[th] = (themeCounts[th] || 0) + 1; }));
  const themeEl = document.getElementById("theme-pills");
  themeEl.innerHTML = "";
  themeEl.appendChild(pillEl(t("themeAll"), upcomingEvents.length, STATE.theme === "all", () => setState({ theme: "all" })));
  Object.keys(themeCounts).sort((a, b) => themeCounts[b] - themeCounts[a]).forEach((theme) => {
    themeEl.appendChild(pillEl(theme, themeCounts[theme], STATE.theme === theme, () => setState({ theme })));
  });

  document.getElementById("sort-select").value = STATE.sort;
  document.getElementById("sort-select").onchange = (e) => setState({ sort: e.target.value });
}
function pillEl(label, count, active, onClick) {
  const b = document.createElement("button");
  b.className = "pill" + (active ? " active" : "");
  b.innerHTML = `<span>${label}</span>` + (count != null ? `<span class="count">${count}</span>` : "");
  b.onclick = onClick;
  return b;
}
function setState(patch) {
  Object.assign(STATE, patch);
  if (patch.theme) bumpPref(patch.theme === "all" ? null : patch.theme, null, 1);
  if (patch.geo && patch.geo !== "all") bumpPref(null, patch.geo, 1);
  render();
}

/* ── Render: tarjeta de evento ───────────────────────────────────────── */
function badgesFor(ev, hotnessP80) {
  const badges = [];
  const acc = ACCOUNTS_BY_USER[ev.sourceAuthor];
  if (acc && acc.betweennessExact != null && acc._bwTopDecile) badges.push({ icon: "ti-git-merge", label: "Puente" });
  if (ev.hotnessScore != null && ev.hotnessScore >= hotnessP80) badges.push({ icon: "ti-trending-up", label: "Resonando" });
  if ((ev.postCount || 1) >= 3) badges.push({ icon: "ti-copy-check", label: "Confirmado x" + ev.postCount });
  return badges.slice(0, 2);
}
function eventCardEl(ev, hotnessP80) {
  const themes = [...eventThemes(ev)];
  const meta = themeMeta(themes[0] || "");
  const card = document.createElement("button");
  card.className = "event-card";
  card.onclick = () => openDetail(ev);
  const badges = badgesFor(ev, hotnessP80);
  card.innerHTML = `
    <div class="event-card-img" style="background:${meta.color}">
      <i class="ti ${meta.icon}" aria-hidden="true"></i>
      ${ev.isFree ? `<span class="badge-free">${t("free")}</span>` : ""}
    </div>
    <div class="event-card-body">
      <p class="event-card-date">${fmtDate(ev.eventDate)}</p>
      <p class="event-card-title">${escapeHtml(ev.title || "")}</p>
      <p class="event-card-loc"><i class="ti ti-map-pin" aria-hidden="true"></i>${escapeHtml(ev.exactAddress || ev.locationName || ev.cityName || "")}</p>
      <div class="event-card-foot">
        <span class="event-card-author">@${escapeHtml((ev.sourceAuthor || "").replace("@", ""))}</span>
        <div class="badge-row">${badges.map((b) => `<span class="badge" title="${b.label}"><i class="ti ${b.icon}" aria-hidden="true"></i></span>`).join("")}</div>
      </div>
    </div>`;
  return card;
}
function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

/* ── Render principal ────────────────────────────────────────────────── */
function render() {
  renderFilterBar();
  const prefs = loadPrefs();
  const filtered = applyFilters(DATA.events);
  const withScore = filtered.map((ev) => ({ ev, score: relevance(ev, prefs) }));

  const hotnessValues = DATA.events.map((e) => e.hotnessScore || 0).sort((a, b) => a - b);
  const hotnessP80 = hotnessValues.length ? hotnessValues[Math.floor(hotnessValues.length * 0.8)] : 0;

  let sorted;
  if (STATE.sort === "date") sorted = [...filtered].sort((a, b) => (a.eventDate || "").localeCompare(b.eventDate || ""));
  else if (STATE.sort === "popularity") sorted = [...filtered].sort((a, b) => (b.hotnessScore || 0) - (a.hotnessScore || 0));
  else sorted = withScore.sort((a, b) => b.score - a.score).map((x) => x.ev);

  const heroSection = document.getElementById("hero-section");
  const shelves = document.getElementById("shelves");
  const resultsCount = document.getElementById("results-count");
  heroSection.innerHTML = ""; shelves.innerHTML = "";

  if (!sorted.length) {
    resultsCount.textContent = "";
    shelves.innerHTML = `<div class="empty-state"><i class="ti ti-map-off" aria-hidden="true"></i><p>${t("emptyTitle")}</p><p style="font-size:13px">${t("emptyBody")}</p></div>`;
    return;
  }

  // "Pasados" (DD-045): sin héroe ni destacados — es una lista plana, sin
  // clasificación, a propósito.
  if (STATE.sort === "recommended" && STATE.when !== "past") {
    const hero = sorted[0];
    const heroMeta = themeMeta([...eventThemes(hero)][0] || "");
    heroSection.innerHTML = `
      <div class="hero-card">
        <div class="hero-img" style="background:${heroMeta.color}"><i class="ti ${heroMeta.icon}" aria-hidden="true"></i></div>
        <div class="hero-body">
          <p class="hero-eyebrow"><i class="ti ti-sparkles" aria-hidden="true"></i>${t("heroEyebrow")}</p>
          <p class="hero-title">${escapeHtml(hero.title || "")}</p>
          <p class="hero-desc">${escapeHtml(hero.description || "")}</p>
          <p class="hero-meta"><span>${fmtDate(hero.eventDate)}</span><span>${escapeHtml(hero.locationName || hero.cityName || "")}</span></p>
        </div>
      </div>`;
    heroSection.querySelector(".hero-card").onclick = () => openDetail(hero);

    const rest = sorted.slice(1);
    const highlights = diversify(rest, 2, 3, 8);
    if (highlights.length) shelves.appendChild(shelfEl(t("shelfHighlights"), t("shelfHighlightsSub"), highlights, hotnessP80, true));

    const freeOnes = rest.filter((e) => e.isFree).slice(0, 8);
    if (!STATE.free && freeOnes.length) shelves.appendChild(shelfEl(t("shelfFree"), t("shelfFreeSub"), freeOnes, hotnessP80, true));
  }

  const gridTitle = STATE.when === "past" ? t("resultsPast")
    : STATE.sort === "recommended" ? t("resultsAll")
    : (STATE.sort === "date" ? t("sortDate") : t("sortPopularity"));
  shelves.appendChild(shelfEl(gridTitle, null, sorted, hotnessP80, false));
  resultsCount.textContent = t("resultsCount", sorted.length);
}
function shelfEl(title, sub, events, hotnessP80, scroll) {
  const wrap = document.createElement("div");
  wrap.className = "shelf";
  wrap.innerHTML = `<p class="shelf-title">${title}</p>` + (sub ? `<p class="shelf-sub">${sub}</p>` : "");
  const list = document.createElement("div");
  list.className = scroll ? "shelf-scroll" : "shelf-grid";
  events.forEach((ev) => list.appendChild(eventCardEl(ev, hotnessP80)));
  wrap.appendChild(list);
  return wrap;
}

/* ── Detalle ─────────────────────────────────────────────────────────── */
function whyReason(ev, prefs) {
  const T = computeT(ev.eventDate), C = computeC(ev, prefs);
  const contribs = [["A", 0.22 * (ev.aScore || 0)], ["B", 0.18 * (ev.bScore || 0)], ["T", 0.20 * T], ["C", 0.10 * C]];
  if ((ev.postCount || 1) >= 3) return t("reasonPosts");
  contribs.sort((a, b) => b[1] - a[1]);
  const top = contribs[0][0];
  if (top === "A") return t("reasonA");
  if (top === "B") return t("reasonB");
  if (top === "T") return t("reasonT");
  return t("reasonC");
}
function openDetail(ev) {
  const prefs = loadPrefs();
  bumpPref([...eventThemes(ev)][0], ev.geoZone, 1);
  const meta = themeMeta([...eventThemes(ev)][0] || "");
  const acc = ACCOUNTS_BY_USER[ev.sourceAuthor] || {};
  const similar = (ev.similarEventIds || []).map((id) => DATA.events.find((e) => e.id === id)).filter(Boolean);
  const panel = document.getElementById("detail-panel");
  panel.innerHTML = `
    <button class="detail-close" data-close aria-label="Cerrar"><i class="ti ti-x" aria-hidden="true"></i></button>
    <div class="detail-img" style="background:${meta.color}"><i class="ti ${meta.icon}" aria-hidden="true"></i></div>
    <div class="detail-grid">
      <div>
        <p class="detail-eyebrow">${fmtDate(ev.eventDate)}</p>
        <h1 class="detail-title">${escapeHtml(ev.title || "")}</h1>
        <p class="detail-desc">${escapeHtml(ev.description || "")}</p>
        <div class="info-box">
          <p class="info-box-label">${t("whatWeKnow")}</p>
          ${ev.exactAddress ? `<div class="info-row"><span class="k"><i class="ti ti-map-pin" aria-hidden="true"></i>${t("address")}</span><span>${escapeHtml(ev.exactAddress)}</span></div>` : ""}
          ${ev.cityName ? `<div class="info-row"><span class="k"><i class="ti ti-building" aria-hidden="true"></i>${t("city")}</span><span>${escapeHtml(ev.cityName)}</span></div>` : ""}
          ${ev.priceRange ? `<div class="info-row"><span class="k"><i class="ti ti-currency-euro" aria-hidden="true"></i>${t("price")}</span><span>${escapeHtml(ev.priceRange)}</span></div>` : ""}
        </div>
        ${ev.sourcePostUrl ? `<a class="cta-link" href="${ev.sourcePostUrl}" target="_blank" rel="noopener" onclick="bumpPref(null,null,2)"><i class="ti ti-external-link" aria-hidden="true"></i>${t("viewOriginal")}</a>` : ""}
        ${similar.length ? `<div style="margin-top:20px;border-top:1px solid var(--border);padding-top:12px">
          <p class="info-box-label">${t("similarEvents")}</p>
          <div class="similar-row">${similar.map((s) => `<div class="similar-card" data-id="${s.id}"><p>${escapeHtml(s.title || "")}</p><p class="d">${fmtDate(s.eventDate)}</p></div>`).join("")}</div>
        </div>` : ""}
        <details class="transparency">
          <summary>${t("detectionDetail")}</summary>
          <p>${t("detectionText", (ev.confidence || 0).toFixed(2), ev.postCount || 1)}</p>
        </details>
      </div>
      <div>
        <div class="sidebar-card">
          <div class="organizer-head">
            <div class="avatar">${(ev.sourceAuthor || "?").slice(0, 2).toUpperCase()}</div>
            <div><p style="margin:0;font-size:13px;font-weight:600">${escapeHtml(ev.sourceAuthor || "")}</p>
            ${acc.followers ? `<p style="margin:0;font-size:11px;color:var(--sub)">${acc.followers.toLocaleString()} ${t("followers")}</p>` : ""}</div>
          </div>
          ${acc.eventFrequency ? `<p style="font-size:12px;color:var(--sub);margin:2px 0">${t("frequency")}: ${escapeHtml(acc.eventFrequency)}</p>` : ""}
          ${acc.hasFreeEvents ? `<p style="font-size:12px;color:var(--sub);margin:2px 0">${t("freeEvents")}: ${escapeHtml(acc.hasFreeEvents)}</p>` : ""}
        </div>
        <div class="why-box"><i class="ti ti-bulb" aria-hidden="true"></i><span>${whyReason(ev, prefs)}</span></div>
        <div class="tag-row">
          ${ev.artType ? escapeHtml(ev.artType).split(",").map((s) => `<span class="tag">${s.trim()}</span>`).join("") : ""}
          ${ev.geoZone ? `<span class="tag">${GEO_LABEL[ev.geoZone] || ev.geoZone}</span>` : ""}
        </div>
      </div>
    </div>`;
  panel.querySelectorAll(".similar-card").forEach((el) => {
    el.onclick = () => { const s = DATA.events.find((e) => e.id === el.dataset.id); if (s) openDetail(s); };
  });
  document.getElementById("detail-overlay").classList.remove("hidden");
  document.querySelectorAll("[data-close]").forEach((el) => (el.onclick = closeDetail));
}
function closeDetail() { document.getElementById("detail-overlay").classList.add("hidden"); }

/* ── Init ────────────────────────────────────────────────────────────── */
function markBetweennessDecile() {
  const vals = DATA.accounts.map((a) => a.betweennessExact).filter((v) => v != null).sort((a, b) => a - b);
  const threshold = vals.length ? vals[Math.floor(vals.length * 0.9)] : Infinity;
  DATA.accounts.forEach((a) => { a._bwTopDecile = a.betweennessExact != null && a.betweennessExact >= threshold; });
}
function initLangButtons() {
  document.querySelectorAll(".lang-btn").forEach((btn) => {
    btn.onclick = () => {
      CURRENT_LANG = btn.dataset.lang;
      document.querySelectorAll(".lang-btn").forEach((b) => b.classList.toggle("active", b === btn));
      render();
    };
  });
}
async function init() {
  initLangButtons();
  document.getElementById("shelves").innerHTML = `<p style="color:var(--sub);font-size:13px">${t("loading")}</p>`;
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    DATA = await res.json();
  } catch (e) {
    DATA = { events: [], accounts: [] };
  }
  ACCOUNTS_BY_USER = {};
  (DATA.accounts || []).forEach((a) => { ACCOUNTS_BY_USER[a.username] = a; });
  markBetweennessDecile();
  render();
}
init();
