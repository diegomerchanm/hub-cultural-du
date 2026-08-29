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

// Retención (DD-071, 2026-08-28): "Pasados" solo muestra hasta
// PAST_RETENTION_DAYS días atrás -- refuerzo en el cliente del mismo corte
// que ya aplica 5_export_dashboard_data.py (--past-days, default 30) al
// generar data.json. Doble capa a propósito: aunque el JSON ya venga
// recortado, el front no debería depender solo de eso para cumplir la regla
// de producto ("en pasados solo lo reciente", pedido de Diego). Debe
// coincidir con PAST_RETENTION_DAYS del script Python -- si uno cambia, el
// otro también.
const PAST_RETENTION_DAYS = 30;

/* Distintas cuentas curadas a mano escribieron la misma zona de dos
   formas ("Francia fuera de IDF" vs "Francia (fuera de Île-de-France)"),
   así que sin esto contaban como dos pills separados con conteos
   partidos, y encima ninguno traducía bien porque el diccionario de abajo
   tenía la clave mal escrita respecto al dato real (faltaba "de" — DD-056,
   2026-08-26, encontrado al auditar site/data.json directamente: 12
   eventos con "Francia fuera de IDF", 11 con "Francia (fuera de
   Île-de-France)", mismo significado). Se normaliza UNA vez al cargar
   DATA (ver init()) para que todo lo demás (conteos, filtro, prefs,
   traducción) trabaje siempre con el valor canónico. */
const GEO_ZONE_SYNONYMS = { "Francia (fuera de Île-de-France)": "Francia fuera de IDF" };
function canonicalizeGeoZone(raw) { return GEO_ZONE_SYNONYMS[raw] || raw; }

/* Mismo problema que geoZone arriba, pero en cityName (2026-08-27, Etapa 1
   de la conversación sobre arquitectura de URLs/SEO -- encontrado auditando
   site/data.json: 100 valores distintos de cityName, con la misma ciudad
   escrita de formas distintas -- "Paris"/"París" (91+75, el caso grande),
   "Marseille"/"Marsella", "Boulogne-Billancourt"/"Boulogne Billancourt",
   "Montreal"/"Montréal", "Ciudad de México"/"Mexico City",
   "Cartagena"/"Cartagena de Indias", "Venice"/"Venecia", y una entrada con
   el string literal "null" en vez de estar vacía de verdad (bug de datos,
   no de sinónimos, pero mismo lugar para arreglarlo). Canónico = ortografía
   francesa para ciudades de Francia (coherente con que ahí es donde vive el
   proyecto y con futuro SEO en francés); para las demás, la forma que ya
   predominaba en el dato real. Aplicado UNA vez al cargar DATA (ver init()),
   mismo patrón que canonicalizeGeoZone. Ojo: esto es un parche defensivo en
   el frontend, no una limpieza de la fuente (Neo4j/planilla) -- si aparecen
   más variantes van a necesitar una entrada nueva acá o, mejor, revisar el
   dato en origen (misma nota que ya dejó DD-056 para geoZone, sigue
   pendiente para los dos campos). */
const CITY_SYNONYMS = {
  "París": "Paris",
  "Boulogne Billancourt": "Boulogne-Billancourt",
  "Montréal": "Montreal",
  "Marsella": "Marseille",
  "Mexico City": "Ciudad de México",
  "Cartagena de Indias": "Cartagena",
  "Venecia": "Venice",
  "null": null,
};
function canonicalizeCityName(raw) {
  if (!raw) return raw;
  return CITY_SYNONYMS.hasOwnProperty(raw) ? CITY_SYNONYMS[raw] : raw;
}
/* Traducción de display para ES (no toca el valor canónico, mismo patrón
   que geoLabel/categoryLabel) -- solo las ciudades de Francia donde el
   nombre en español diverge lo suficiente como para notarse; para el resto
   (Bogotá, Medellín, Nueva York, etc.) el canónico ya sirve en los dos
   idiomas. Deliberadamente NO se usa en mapEmbedHtml/directionsUrl/gcalUrl
   -- esas funciones arman queries para Google, más confiable pasarle
   siempre la ortografía canónica/francesa que la traducida. */
const CITY_LABEL_ES = { "Paris": "París", "Marseille": "Marsella" };
function cityLabel(city) {
  if (!city) return city;
  if (CURRENT_LANG === "es" && CITY_LABEL_ES[city]) return CITY_LABEL_ES[city];
  return city;
}

/* GEO_LABEL / categoría en español quedan como identidad canónica (así no
   se pisan los catWeights ya guardados en localStorage de visitantes
   existentes, ver bumpPref/loadPrefs) — la traducción para mostrar pasa
   por geoLabel()/categoryLabel() de abajo, que leen de i18n.js según
   CURRENT_LANG y caen a este mismo valor si no hay traducción. */
const GEO_LABEL = { "Île-de-France": "Île-de-France", "Francia fuera de IDF": "Francia (fuera IDF)", "Fuera de Francia": "Fuera de Francia" };
function geoLabel(zone) {
  const dict = I18N[CURRENT_LANG].geoLabels;
  return (dict && dict[zone]) || GEO_LABEL[zone] || zone;
}
function categoryLabel(key) {
  const dict = I18N[CURRENT_LANG].categories;
  return (dict && dict[key]) || (CATEGORY_META[key] && CATEGORY_META[key].label) || key;
}
/* Traducción de eventArtTags (tags libres del LLM, DD-042) al francés,
   solo disponible para eventos creados desde el 2026-08 (nueva propiedad
   eventArtTagsFr, alineada por posición con eventArtTags — ver DD-054).
   Se arma un diccionario global ES→FR recorriendo todos los eventos una
   vez, para poder traducir un tag sin importar en qué evento se lo mire. */
let TAG_FR_MAP = {};
function buildTagFrMap() {
  TAG_FR_MAP = {};
  DATA.events.forEach((ev) => {
    const tags = ev.eventArtTags || [], tagsFr = ev.eventArtTagsFr || [];
    tags.forEach((tag, i) => { if (tagsFr[i]) TAG_FR_MAP[tag] = tagsFr[i]; });
  });
}
/* Label visible de un theme (canónico en español, sea categoría fija o tag
   libre) — categorías fijas resuelven por key vía categoryLabel(); tags
   libres resuelven por TAG_FR_MAP si hay traducción, si no se quedan en
   español (mismo patrón de fallback que evTitle/evDescription). */
function themeLabel(themeEs) {
  const catEntry = Object.entries(CATEGORY_META).find(([, v]) => v.label === themeEs);
  if (catEntry) return categoryLabel(catEntry[0]);
  if (CURRENT_LANG === "fr" && TAG_FR_MAP[themeEs]) return TAG_FR_MAP[themeEs];
  return themeEs;
}
/* Color de un theme para el filtro (DD-055): las 11 categorías fijas usan
   el color ya curado en CATEGORY_META; los tags libres del LLM (sin color
   propio) reciben uno generado determinísticamente a partir del texto en
   español (mismo tag = mismo color siempre, sin depender de random ni de
   guardar nada — se recalcula igual en cada visita). Se devuelve como
   borde+fondo tenue (no relleno sólido) porque con 50+ tonos distintos un
   fondo saturado sería ilegible o rompería el contraste del texto. */
function hashColor(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) { hash = str.charCodeAt(i) + ((hash << 5) - hash); hash |= 0; }
  return `hsl(${Math.abs(hash) % 360}, 55%, 42%)`;
}
function themeColor(themeEs) {
  const catEntry = Object.entries(CATEGORY_META).find(([, v]) => v.label === themeEs);
  const solid = catEntry ? catEntry[1].color : hashColor(themeEs);
  return { solid, tint: `color-mix(in srgb, ${solid} 12%, var(--card))` };
}

const PREFS_KEY = "hcdu_prefs";

let DATA = { events: [], accounts: [] };
let ACCOUNTS_BY_USER = {};

/* Título/descripción del evento en el idioma actual (CURRENT_LANG, ver
   i18n.js). Los campos *Fr solo existen en eventos creados desde el
   2026-08-24 (DD-051) — 4_enrich_events_extract.py ya no hace backfill de
   eventos viejos, así que si faltan, caemos al español en vez de dejar un
   hueco vacío en modo FR. */
function evTitle(ev) { return (CURRENT_LANG === "fr" && ev.titleFr) ? ev.titleFr : (ev.title || ""); }
function evDescription(ev) { return (CURRENT_LANG === "fr" && ev.descriptionFr) ? ev.descriptionFr : (ev.description || ""); }
let STATE = { geo: "all", when: "upcoming", theme: "all", free: false, sort: "recommended", userLocation: null, search: "" };

/* ── Geolocalización opcional (DD-057): reemplaza al mapa Leaflet, que
   Diego pidió sacar del todo tras verlo desplegado con un filtro de color
   que no terminó de andar bien. En vez de mostrar un mapa embebido, se le
   puede pedir al visitante su ubicación (gesto explícito, nunca automático
   al cargar la página) para ordenar los eventos por cercanía real —
   no se persiste en localStorage ni se manda a ningún lado, vive solo en
   memoria mientras dura la visita. */
function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1), dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
function requestLocation() {
  if (!navigator.geolocation) return;
  const btn = document.getElementById("geo-locate-toggle");
  if (btn) btn.disabled = true;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      STATE.userLocation = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      STATE.sort = "distance";
      render();
    },
    () => { render(); }, // permiso denegado o error -- no se cambia nada, solo se re-habilita el botón
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 }
  );
}

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
/* "Este finde" (2026-08-27, pedido de Diego): sábado y domingo más próximos,
   nunca en el pasado. Si hoy es sábado, el finde es hoy+mañana; si es
   domingo, el finde ya empezó y termina hoy (no incluye el sábado que ya
   pasó); cualquier otro día, el finde es el próximo sábado y domingo.
   Función aparte de whenBucket() a propósito -- whenBucket() devuelve UN
   bucket mutuamente excluyente por evento y ya lo usan las pills de
   hoy/semana, agregarle "weekend" ahí rompería esa exclusividad (un evento
   de sábado ya cuenta como "week" también, y así debe seguir contando). */
function isWeekendEvent(dateStr) {
  const d = daysUntil(dateStr);
  if (d === null || d < 0) return false;
  const dow = new Date().getDay(); // 0=dom..6=sáb, día de HOY
  if (dow === 0) return d === 0;
  if (dow === 6) return d === 0 || d === 1;
  const satIn = 6 - dow;
  return d === satIn || d === satIn + 1;
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
function fmtDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr + "T00:00:00");
  if (isNaN(d)) return dateStr;
  const months = I18N[CURRENT_LANG].months;
  return `${d.getDate()} ${months[d.getMonth()]}`;
}

/* ── Buscador (2026-08-27, pedido de Diego) ─────────────────────────────
   Cliente-side sobre DATA.events, ya está todo cargado -- sin backend, sin
   índice, solo un .includes() sobre un puñado de campos. normalizeSearch()
   saca acentos (NFD + strip de diacríticos) para que "musica" encuentre
   "música" y "cine frances" encuentre "cine francés" -- útil en un sitio
   bilingüe ES/FR donde el visitante puede escribir sin tildes. */
function normalizeSearch(s) {
  return (s || "").toString().normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}
function matchesSearch(ev, query) {
  if (!query) return true;
  const q = normalizeSearch(query);
  if (!q) return true;
  const tags = Array.isArray(ev.eventArtTags) ? ev.eventArtTags : [ev.eventArtTags];
  // CITY_LABEL_ES directo (no cityLabel(), que solo devuelve la variante ES
  // cuando CURRENT_LANG === "es" -- acá conviene que "marsella" encuentre
  // Marseille sin importar en qué idioma esté el sitio en ese momento).
  const haystack = [
    evTitle(ev), evDescription(ev), ev.locationName, ev.cityName, CITY_LABEL_ES[ev.cityName], ev.exactAddress,
    ev.sourceAuthor, ev.artType, ...tags,
  ].filter(Boolean).join(" ");
  return normalizeSearch(haystack).includes(q);
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
      const d = daysUntil(ev.eventDate);
      if (d === null || d < -PAST_RETENTION_DAYS) return false;
      // DD-071: refuerzo de retención en cliente — ver comentario junto a
      // PAST_RETENTION_DAYS más arriba.
    } else if (STATE.when !== "upcoming") {
      const bucket = whenBucket(ev.eventDate);
      if (bucket === "past" || bucket === null) return false;
      if (STATE.when === "today" && bucket !== "today") return false;
      if (STATE.when === "weekend" && !isWeekendEvent(ev.eventDate)) return false;
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
    if (!matchesSearch(ev, STATE.search)) return false;
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
    geoEl.appendChild(pillEl(geoLabel(zone), zoneCounts[zone], STATE.geo === zone, () => setState({ geo: zone })));
  });

  // Pills de fecha, cada una con su propio conteo (independiente entre sí,
  // sobre el dataset completo) — "Pasados" es el complemento de "por venir".
  const whenEl = document.getElementById("when-pills");
  whenEl.innerHTML = "";
  const todayN = DATA.events.filter((ev) => whenBucket(ev.eventDate) === "today").length;
  const weekendN = DATA.events.filter((ev) => isWeekendEvent(ev.eventDate)).length;
  const weekN = DATA.events.filter((ev) => { const b = whenBucket(ev.eventDate); return b === "today" || b === "week"; }).length;
  const monthN = DATA.events.filter((ev) => { const d = daysUntil(ev.eventDate); return d !== null && d >= 0 && d <= 31; }).length;
  const upcomingN = upcomingEvents.length;
  const pastN = DATA.events.length - upcomingN;
  [
    ["today", t("whenToday"), todayN],
    ["weekend", t("whenWeekend"), weekendN],
    ["week", t("whenWeek"), weekN],
    ["month", t("whenMonth"), monthN],
    ["upcoming", t("whenUpcoming"), upcomingN],
    ["past", t("whenPast"), pastN],
  ].forEach(([key, label, n]) => whenEl.appendChild(pillEl(label, n, STATE.when === key, () => setState({ when: key }))));

  // Buscador (2026-08-27): input estático de index.html, no se reconstruye
  // en cada render -- solo se sincroniza el valor (no-op si ya coincide, no
  // pisa lo que el visitante está tirando) y se cablea el handler, mismo
  // patrón que free-toggle/geo-locate-toggle de acá abajo.
  const searchInput = document.getElementById("search-input");
  if (searchInput) {
    if (searchInput.value !== STATE.search) searchInput.value = STATE.search;
    searchInput.oninput = () => setState({ search: searchInput.value });
  }

  const freeBtn = document.getElementById("free-toggle");
  freeBtn.classList.toggle("active", STATE.free);
  freeBtn.onclick = () => setState({ free: !STATE.free });

  const geoLocateBtn = document.getElementById("geo-locate-toggle");
  geoLocateBtn.disabled = false;
  geoLocateBtn.classList.toggle("active", !!STATE.userLocation);
  geoLocateBtn.onclick = () => {
    if (STATE.userLocation) {
      STATE.userLocation = null;
      if (STATE.sort === "distance") STATE.sort = "recommended";
      render();
    } else {
      requestLocation();
    }
  };

  // DD-055/DD-056: con 50+ categorías/tags posibles, mostrarlas todas al
  // mismo tamaño era caótico. Primer intento (DD-055) agregaba una fila
  // "Otras categorías" para los tags de 1-2 eventos en vez de ocultarlos
  // del todo -- Diego la vio desplegada y pidió sacarla directamente
  // (seguía siendo ruido, y cada uno de esos eventos ya es encontrable por
  // su categoría principal de todas formas). Ahora los tags con 2 eventos
  // o menos simplemente no arman pill en el menú -- el evento sigue
  // existiendo y filtrable por su categoría fija, solo que ese tag puntual
  // no ensucia el menú. Los que sí entran arman la nube con 3 tamaños
  // (terciles dinámicos sobre el volumen real, no umbrales fijos).
  const MIN_THEME_COUNT = 3;
  const themeCounts = {};
  upcomingEvents.forEach((ev) => eventThemes(ev).forEach((th) => { themeCounts[th] = (themeCounts[th] || 0) + 1; }));
  const mainThemes = Object.keys(themeCounts)
    .map((theme) => ({ theme, count: themeCounts[theme] }))
    .filter((e) => e.count >= MIN_THEME_COUNT)
    .sort((a, b) => b.count - a.count);
  const tierFor = (i, n) => {
    if (n <= 1) return "tier-lg";
    if (i < n / 3) return "tier-lg";
    if (i < (n / 3) * 2) return "tier-md";
    return "tier-sm";
  };

  const themeEl = document.getElementById("theme-pills");
  themeEl.innerHTML = "";
  themeEl.appendChild(pillEl(t("themeAll"), upcomingEvents.length, STATE.theme === "all", () => setState({ theme: "all" }), "tier-lg"));
  mainThemes.forEach(({ theme, count }, i) => {
    themeEl.appendChild(pillEl(
      themeLabel(theme), count, STATE.theme === theme, () => setState({ theme }),
      tierFor(i, mainThemes.length), themeColor(theme)
    ));
  });

  document.getElementById("sort-select").value = STATE.sort;
  document.getElementById("sort-select").onchange = (e) => {
    // Elegir "Cercanía" sin haber dado ubicación todavía dispara el
    // permiso -- el sort se aplica recién cuando (si) el navegador
    // resuelve la posición (ver requestLocation), no antes.
    if (e.target.value === "distance" && !STATE.userLocation) { requestLocation(); return; }
    setState({ sort: e.target.value });
  };
}
function pillEl(label, count, active, onClick, tierClass, colorAccent) {
  const b = document.createElement("button");
  b.className = "pill" + (tierClass ? " " + tierClass : "") + (active ? " active" : "");
  // El acento de color solo se aplica inactivo -- un estilo inline tiene
  // más especificidad que cualquier clase, así que si lo dejáramos puesto
  // en estado activo taparía el fondo oscuro de .pill.active sin querer.
  if (colorAccent && !active) {
    b.style.borderLeft = `3px solid ${colorAccent.solid}`;
    b.style.background = colorAccent.tint;
  }
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

/* Solo se muestra cuando el visitante activó "Cerca de mí" -- si no hay
   ubicación, no se calcula ninguna distancia por defecto. */
function distanceLabel(ev) {
  if (!STATE.userLocation || ev.lat == null || ev.lon == null) return "";
  const km = haversineKm(STATE.userLocation.lat, STATE.userLocation.lon, ev.lat, ev.lon);
  return ` · ${km < 1 ? Math.round(km * 1000) + " m" : km.toFixed(1) + " km"}`;
}

/* Foto real del post original (DD-057) cuando existe, con degradación al
   diseño de color+ícono de siempre si falta o si la URL deja de cargar --
   son links firmados de la CDN de Instagram, capturados en el momento del
   scrape, y pueden expirar con el tiempo. El manejo de error se hace con
   un listener real después de insertar el HTML (attachImageFallback), no
   con un onerror inline: mucho más simple que escapar comillas dentro de
   un atributo HTML a mano. */
/* DD-060: la foto real solo se muestra si la cuenta autorizó explícitamente
   (columna "Permiso de foto" de la planilla curada, load_manual_account_
   categorization.py). Sin autorización explícita (incluye null = cuenta
   sin contactar todavía), nunca se muestra ev.imageUrl en tarjetas/hero —
   ahí cae al ícono+color de siempre. En el panel de detalle hay un tercer
   estado (embed oficial de Instagram) manejado aparte, ver detailMediaHtml. */
function hasPhotoPermission(ev) {
  return ev.photoPermission === true && !!ev.imageUrl;
}
function imageBlockHtml(ev, meta, imgClass) {
  if (!hasPhotoPermission(ev)) return `<i class="ti ${meta.icon}" aria-hidden="true"></i>`;
  return `<img src="${escapeHtml(ev.imageUrl)}" alt="" class="${imgClass}" loading="lazy">`;
}
function attachImageFallback(container, meta) {
  const img = container && container.querySelector("img");
  if (!img) return;
  img.onerror = () => {
    img.remove();
    container.style.background = meta.color;
    container.insertAdjacentHTML("afterbegin", `<i class="ti ${meta.icon}" aria-hidden="true"></i>`);
  };
}

/* DD-060: embed oficial de Instagram (oEmbed/blockquote), usado SOLO en el
   panel de detalle cuando la cuenta no autorizó mostrar su foto real. A
   diferencia de copiar/alojar la imagen, esto renderiza el post en vivo
   desde los servidores de Instagram — no hay archivo nuestro de por medio.
   El <blockquote> ya trae un <a> real al post como contenido de reserva,
   así que si el script no carga o Instagram no lo procesa, igual queda un
   link funcional en vez de una caja rota. */
let igEmbedScriptPromise = null;
function loadInstagramEmbedScript() {
  if (igEmbedScriptPromise) return igEmbedScriptPromise;
  igEmbedScriptPromise = new Promise((resolve) => {
    if (window.instgrm) return resolve();
    const s = document.createElement("script");
    s.src = "https://www.instagram.com/embed.js";
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => resolve(); // degrada en silencio -- el link dentro del blockquote sigue sirviendo
    document.body.appendChild(s);
  });
  return igEmbedScriptPromise;
}
/* Mapa embebido de Google (Maps Embed API), solo en el panel de detalle.
   Requiere una API key (gratis, uso ilimitado según docs de Google 2026,
   pero necesita un proyecto de Google Cloud con facturación activada) --
   la key vive en site/config.js, fuera de este archivo, para que Diego
   pueda pegarla sin tocar código. Si no hay key configurada, o el evento
   no tiene ni coordenadas ni dirección, no se renderiza nada (silencioso,
   no rompe el resto del panel). Restringir la key por HTTP referrer al
   dominio del sitio en la consola de Google Cloud -- es una key de
   frontend, siempre queda visible en el HTML, la restricción es lo que
   evita el uso indebido, no el secreto. */
function mapEmbedHtml(ev) {
  const key = window.GOOGLE_MAPS_EMBED_KEY;
  if (!key || key === "YOUR_KEY_HERE") return "";
  const q = (ev.lat != null && ev.lon != null)
    ? `${ev.lat},${ev.lon}`
    : [ev.exactAddress, ev.locationName, ev.cityName].filter(Boolean).join(", ");
  if (!q) return "";
  const src = `https://www.google.com/maps/embed/v1/place?key=${encodeURIComponent(key)}&q=${encodeURIComponent(q)}`;
  return `<div class="detail-map"><iframe src="${src}" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen title="${escapeHtml(t("mapTitle"))}"></iframe></div>`;
}
/* "Cómo llegar" -- esquema de URL pública de Google Maps (Universal Maps
   URLs), no una API paga: no hace falta key ni facturación, es el mismo
   link que arma el botón "Cómo llegar" de Google Maps normal. Prioriza
   lat/lon (ya geocodificado, más preciso) sobre la dirección de texto. */
function directionsUrl(ev) {
  const dest = (ev.lat != null && ev.lon != null)
    ? `${ev.lat},${ev.lon}`
    : (ev.exactAddress || ev.locationName || ev.cityName || "");
  return dest ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(dest)}` : null;
}

/* "Agregar a Google Calendar" -- URL de plantilla documentada de Google
   Calendar, tampoco es una API paga, es un link. Como eventDate es solo
   fecha (sin hora, no la tenemos con confianza suficiente en la extracción
   actual), se arma como evento de todo el día: Google Calendar espera la
   fecha de fin EXCLUSIVA, por eso se suma un día. */
function gcalUrl(ev) {
  if (!ev.eventDate) return null;
  const start = new Date(ev.eventDate + "T00:00:00");
  if (isNaN(start)) return null;
  const end = new Date(start); end.setDate(end.getDate() + 1);
  const ymd = (d) => `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
  const details = evDescription(ev) + (ev.sourcePostUrl ? `\n\n${ev.sourcePostUrl}` : "");
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: evTitle(ev),
    dates: `${ymd(start)}/${ymd(end)}`,
    details,
    location: ev.exactAddress || ev.locationName || ev.cityName || "",
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

/* Compartir un evento puntual (2026-08-27, pedido de Diego). El sitio no
   tenía links propios por evento -- abrir un evento nunca cambiaba la URL,
   así que compartir hasta ahora solo podía compartir el home. eventShareUrl
   arma un link con ?evento=<id> sobre la misma página (sin ruta nueva en
   Cloudflare Workers, es un query string sobre "/"); openDetail/closeDetail
   lo empujan a la barra de direcciones con history.pushState (sin recargar
   la página) y lo retiran al cerrar. init() revisa ese parámetro al cargar
   para poder abrir un evento directo desde un link compartido. */
function eventShareUrl(ev) {
  return `${location.origin}${location.pathname}?evento=${encodeURIComponent(ev.id)}`;
}
async function shareEvent(ev, btn) {
  const url = eventShareUrl(ev);
  if (navigator.share) {
    try { await navigator.share({ title: evTitle(ev), text: evDescription(ev).slice(0, 140), url }); }
    catch (e) { /* el visitante canceló el panel nativo de compartir -- no es un error */ }
    return;
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try { await navigator.clipboard.writeText(url); flashShareFeedback(btn); }
    catch (e) { /* portapapeles bloqueado por el navegador -- el link sigue visible en la barra de direcciones */ }
  }
}
function flashShareFeedback(btn) {
  const label = btn && btn.querySelector(".share-label");
  if (!label) return;
  const original = label.textContent;
  label.textContent = t("linkCopied");
  btn.disabled = true;
  setTimeout(() => { label.textContent = original; btn.disabled = false; }, 1500);
}

function detailMediaHtml(ev, meta) {
  if (hasPhotoPermission(ev)) {
    return { kind: "photo", html: `<img src="${escapeHtml(ev.imageUrl)}" alt="" class="detail-photo" loading="lazy">` };
  }
  if (ev.sourcePostUrl) {
    const url = escapeHtml(ev.sourcePostUrl);
    return {
      kind: "embed",
      html: `<blockquote class="instagram-media" data-instgrm-permalink="${url}" data-instgrm-version="14">
        <a href="${url}" target="_blank" rel="noopener">${t("viewOriginal")}</a>
      </blockquote>`,
    };
  }
  return { kind: "icon", html: `<i class="ti ${meta.icon}" aria-hidden="true"></i>` };
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
    <div class="event-card-img" style="${hasPhotoPermission(ev) ? "" : `background:${meta.color}`}">
      ${imageBlockHtml(ev, meta, "event-card-photo")}
      ${ev.isFree ? `<span class="badge-free">${t("free")}</span>` : ""}
    </div>
    <div class="event-card-body">
      <p class="event-card-date">${fmtDate(ev.eventDate)}</p>
      <p class="event-card-title">${escapeHtml(evTitle(ev))}</p>
      <p class="event-card-loc"><i class="ti ti-map-pin" aria-hidden="true"></i>${escapeHtml(ev.exactAddress || ev.locationName || cityLabel(ev.cityName) || "")}${distanceLabel(ev)}</p>
      <div class="event-card-foot">
        <span class="event-card-author">@${escapeHtml((ev.sourceAuthor || "").replace("@", ""))}</span>
        <div class="badge-row">${badges.map((b) => `<span class="badge" title="${b.label}"><i class="ti ${b.icon}" aria-hidden="true"></i></span>`).join("")}</div>
      </div>
    </div>`;
  attachImageFallback(card.querySelector(".event-card-img"), meta);
  return card;
}
function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

/* Etiquetas estáticas de index.html (Dónde/Cuándo/Ordenar/Gratis/Ver
   mapa/opciones del <select> de orden) llevaban un atributo data-i18n
   desde el principio, pero nada lo leía — quedaban fijas en español pase
   lo que pase con el botón ES/FR (encontrado 2026-08-26, junto con
   categorías/geoZone/fecha, ver DD-054). Genérico a propósito: cualquier
   data-i18n nuevo que se agregue a futuro en index.html se traduce solo,
   sin tocar app.js de nuevo. */
function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const val = t(el.dataset.i18n);
    if (val != null) el.textContent = val;
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const val = t(el.dataset.i18nPlaceholder);
    if (val != null) el.placeholder = val;
  });
}

/* ── Render principal ────────────────────────────────────────────────── */
function render() {
  applyStaticI18n();
  renderFilterBar();
  const prefs = loadPrefs();
  const filtered = applyFilters(DATA.events);

  const heroSection = document.getElementById("hero-section");
  const shelves = document.getElementById("shelves");
  const resultsCount = document.getElementById("results-count");

  const withScore = filtered.map((ev) => ({ ev, score: relevance(ev, prefs) }));

  const hotnessValues = DATA.events.map((e) => e.hotnessScore || 0).sort((a, b) => a - b);
  const hotnessP80 = hotnessValues.length ? hotnessValues[Math.floor(hotnessValues.length * 0.8)] : 0;

  let sorted;
  if (STATE.sort === "date") sorted = [...filtered].sort((a, b) => (a.eventDate || "").localeCompare(b.eventDate || ""));
  else if (STATE.sort === "popularity") sorted = [...filtered].sort((a, b) => (b.hotnessScore || 0) - (a.hotnessScore || 0));
  else if (STATE.sort === "distance" && STATE.userLocation) {
    const { lat, lon } = STATE.userLocation;
    // Eventos sin lat/lon no desaparecen (a diferencia del mapa viejo, que
    // los ocultaba sin avisar) -- se van al final con distancia infinita,
    // siguen viéndose en la lista, solo que no ordenados por cercanía.
    sorted = filtered
      .map((ev) => ({ ev, d: (ev.lat != null && ev.lon != null) ? haversineKm(lat, lon, ev.lat, ev.lon) : Infinity }))
      .sort((a, b) => a.d - b.d)
      .map((x) => x.ev);
  }
  else sorted = withScore.sort((a, b) => b.score - a.score).map((x) => x.ev);

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
        <div class="hero-img" style="${hasPhotoPermission(hero) ? "" : `background:${heroMeta.color}`}">${imageBlockHtml(hero, heroMeta, "hero-photo")}</div>
        <div class="hero-body">
          <p class="hero-eyebrow"><i class="ti ti-sparkles" aria-hidden="true"></i>${t("heroEyebrow")}</p>
          <p class="hero-title">${escapeHtml(evTitle(hero))}</p>
          <p class="hero-desc">${escapeHtml(evDescription(hero))}</p>
          <p class="hero-meta"><span>${fmtDate(hero.eventDate)}</span><span>${escapeHtml(hero.locationName || cityLabel(hero.cityName) || "")}</span></p>
        </div>
      </div>`;
    heroSection.querySelector(".hero-card").onclick = () => openDetail(hero);
    attachImageFallback(heroSection.querySelector(".hero-img"), heroMeta);

    const rest = sorted.slice(1);
    const highlights = diversify(rest, 2, 3, 8);
    if (highlights.length) shelves.appendChild(shelfEl(t("shelfHighlights"), t("shelfHighlightsSub"), highlights, hotnessP80, true));

    const freeOnes = rest.filter((e) => e.isFree).slice(0, 8);
    if (!STATE.free && freeOnes.length) shelves.appendChild(shelfEl(t("shelfFree"), t("shelfFreeSub"), freeOnes, hotnessP80, true));
  }

  const gridTitle = STATE.when === "past" ? t("resultsPast")
    : STATE.sort === "recommended" ? t("resultsAll")
    : STATE.sort === "date" ? t("sortDate")
    : STATE.sort === "distance" ? t("sortDistance")
    : t("sortPopularity");
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
function openDetail(ev, opts = {}) {
  const { updateHistory = true } = opts;
  const prefs = loadPrefs();
  bumpPref([...eventThemes(ev)][0], ev.geoZone, 1);
  const meta = themeMeta([...eventThemes(ev)][0] || "");
  const acc = ACCOUNTS_BY_USER[ev.sourceAuthor] || {};
  const similar = (ev.similarEventIds || []).map((id) => DATA.events.find((e) => e.id === id)).filter(Boolean);
  const panel = document.getElementById("detail-panel");
  const chips = [
    ev.culturalIdentity ? `<span class="chip chip-identity">${t("cultIdLabel")}: ${escapeHtml(ev.culturalIdentity)}</span>` : "",
    ev.institutionType ? `<span class="chip chip-institution">${t("instTypeLabel")}: ${escapeHtml(ev.institutionType)}</span>` : "",
    ev.geoZone ? `<span class="chip chip-geo">${geoLabel(ev.geoZone)}</span>` : "",
  ].filter(Boolean).join("");
  const media = detailMediaHtml(ev, meta);
  // El embed de Instagram va abajo, después de nuestra info (pedido de Diego,
  // 2026-08-27): arriba del todo siempre va el ícono+color de siempre (igual
  // que el caso "sin foto"), y el <blockquote> real se inserta al final del
  // tab "Resumen", después del link "Ver original". La foto real (cuando hay
  // permiso) sigue arriba, ahí sí funciona como imagen destacada.
  const topMedia = media.kind === "embed" ? { kind: "icon", html: `<i class="ti ${meta.icon}" aria-hidden="true"></i>` } : media;
  panel.innerHTML = `
    <button class="detail-close" data-close aria-label="Cerrar"><i class="ti ti-x" aria-hidden="true"></i></button>
    <div class="detail-img" style="${topMedia.kind === "icon" ? `background:${meta.color}` : ""}">${topMedia.html}</div>
    <div class="detail-tabs">
      <button class="detail-tab active" data-tab="summary">${t("tabSummary")}</button>
      <button class="detail-tab" data-tab="more">${t("tabMoreInfo")}</button>
    </div>
    <div class="detail-pane" data-pane="summary">
      <p class="detail-eyebrow">${fmtDate(ev.eventDate)}</p>
      <h1 class="detail-title">${escapeHtml(evTitle(ev))}</h1>
      ${chips ? `<div class="chip-row">${chips}</div>` : ""}
      <p class="detail-desc">${escapeHtml(evDescription(ev))}</p>
      <div class="info-box">
        <p class="info-box-label">${t("whatWeKnow")}</p>
        ${ev.exactAddress ? `<div class="info-row"><span class="k"><i class="ti ti-map-pin" aria-hidden="true"></i>${t("address")}</span><span>${escapeHtml(ev.exactAddress)}</span></div>` : ""}
        ${ev.cityName ? `<div class="info-row"><span class="k"><i class="ti ti-building" aria-hidden="true"></i>${t("city")}</span><span>${escapeHtml(cityLabel(ev.cityName))}</span></div>` : ""}
        ${ev.priceRange ? `<div class="info-row"><span class="k"><i class="ti ti-currency-euro" aria-hidden="true"></i>${t("price")}</span><span>${escapeHtml(ev.priceRange)}</span></div>` : ""}
      </div>
      ${mapEmbedHtml(ev)}
      <div class="action-row">
        <button type="button" class="cta-link share-btn"><i class="ti ti-share-3" aria-hidden="true"></i><span class="share-label">${t("share")}</span></button>
        ${directionsUrl(ev) ? `<a class="cta-link" href="${directionsUrl(ev)}" target="_blank" rel="noopener"><i class="ti ti-directions" aria-hidden="true"></i>${t("directions")}</a>` : ""}
        ${gcalUrl(ev) ? `<a class="cta-link" href="${gcalUrl(ev)}" target="_blank" rel="noopener"><i class="ti ti-calendar-plus" aria-hidden="true"></i>${t("addToCalendar")}</a>` : ""}
        ${ev.sourcePostUrl ? `<a class="cta-link" href="${ev.sourcePostUrl}" target="_blank" rel="noopener" onclick="bumpPref(null,null,2)"><i class="ti ti-external-link" aria-hidden="true"></i>${t("viewOriginal")}</a>` : ""}
      </div>
      ${media.kind === "embed" ? `<div class="detail-embed">${media.html}</div>` : ""}
    </div>
    <div class="detail-pane hidden" data-pane="more">
      <div class="sidebar-card">
        <div class="organizer-head">
          <div class="avatar">${(ev.sourceAuthor || "?").slice(0, 2).toUpperCase()}</div>
          <div><p style="margin:0;font-size:13px;font-weight:600">${escapeHtml(ev.sourceAuthor || "")}</p>
          ${acc.followers ? `<p style="margin:0;font-size:11px;color:var(--sub)">${acc.followers.toLocaleString()} ${t("followers")}</p>` : ""}</div>
        </div>
        ${acc.eventFrequency ? `<p style="font-size:12px;color:var(--sub);margin:2px 0">${t("frequency")}: ${escapeHtml(acc.eventFrequency)}</p>` : ""}
        ${acc.hasFreeEvents ? `<p style="font-size:12px;color:var(--sub);margin:2px 0">${t("freeEvents")}: ${escapeHtml(acc.hasFreeEvents)}</p>` : ""}
        ${ev.parentInstitution ? `<p style="font-size:12px;color:var(--sub);margin:2px 0">${t("parentInstitutionLabel")}: ${escapeHtml(ev.parentInstitution)}</p>` : ""}
      </div>
      <div class="why-box"><i class="ti ti-bulb" aria-hidden="true"></i><span>${whyReason(ev, prefs)}</span></div>
      ${ev.artType ? `<div class="tag-row">${escapeHtml(ev.artType).split(",").map((s) => `<span class="tag">${s.trim()}</span>`).join("")}</div>` : ""}
      ${similar.length ? `<div style="margin-top:16px;border-top:1px solid var(--border);padding-top:12px">
        <p class="info-box-label">${t("similarEvents")}</p>
        <div class="similar-row">${similar.map((s) => `<div class="similar-card" data-id="${s.id}"><p>${escapeHtml(evTitle(s))}</p><p class="d">${fmtDate(s.eventDate)}</p></div>`).join("")}</div>
      </div>` : ""}
      <details class="transparency">
        <summary>${t("detectionDetail")}</summary>
        <p>${t("detectionText", (ev.confidence || 0).toFixed(2), ev.postCount || 1)}</p>
      </details>
    </div>`;
  if (media.kind === "photo") {
    attachImageFallback(panel.querySelector(".detail-img"), meta);
  } else if (media.kind === "embed") {
    loadInstagramEmbedScript().then(() => {
      if (window.instgrm && window.instgrm.Embeds) window.instgrm.Embeds.process();
    });
  }
  const shareBtn = panel.querySelector(".share-btn");
  if (shareBtn) shareBtn.onclick = () => shareEvent(ev, shareBtn);
  panel.querySelectorAll(".similar-card").forEach((el) => {
    el.onclick = () => { const s = DATA.events.find((e) => e.id === el.dataset.id); if (s) openDetail(s); };
  });
  panel.querySelectorAll(".detail-tab").forEach((btn) => {
    btn.onclick = () => {
      panel.querySelectorAll(".detail-tab").forEach((b) => b.classList.toggle("active", b === btn));
      panel.querySelectorAll(".detail-pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== btn.dataset.tab));
      panel.scrollTop = 0;
    };
  });
  document.getElementById("detail-overlay").classList.remove("hidden");
  document.querySelectorAll("[data-close]").forEach((el) => (el.onclick = closeDetail));
  if (updateHistory) {
    const url = eventShareUrl(ev);
    if (location.href !== url) history.pushState({ eventId: ev.id }, "", url);
  }
}
function closeDetail(opts = {}) {
  const { updateHistory = true } = opts;
  document.getElementById("detail-overlay").classList.add("hidden");
  if (updateHistory && new URLSearchParams(location.search).get("evento")) {
    history.pushState(null, "", location.pathname);
  }
}
/* Atrás/adelante del navegador sobre un link de evento (?evento=id): no
   volvemos a pushear historial acá (updateHistory: false), la URL ya la
   cambió el propio navegador al navegar -- solo hay que reflejarla en el
   panel. */
window.addEventListener("popstate", () => {
  const id = new URLSearchParams(location.search).get("evento");
  const ev = id && DATA.events ? DATA.events.find((e) => e.id === id) : null;
  if (ev) openDetail(ev, { updateHistory: false });
  else closeDetail({ updateHistory: false });
});

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
/* Deep-link de filtros iniciales (2026-08-29, Etapa 3, DD-072): las páginas
   estáticas de /categoria/ y /francia/ (6_generate_seo_pages.py) enlazan de
   vuelta al sitio interactivo con ?tema=/?geo=/?buscar= para que "ver estos
   eventos en el sitio interactivo" aterrice ya filtrado, en vez de mandar al
   visitante a la home sin filtro y que tenga que reencontrar el mismo
   recorte a mano. Mismo espíritu que ?evento=<id> (DD-063) pero para una
   LISTA de eventos, no uno solo -- por eso esta función muta STATE
   directo (Object.assign implícito vía asignación) en vez de pasar por
   setState(): un link entrante no debería contar como una interacción real
   para bumpPref()/catWeights (pensados para reflejar clicks genuinos del
   visitante durante la sesión, no de dónde vino el link de entrada), y
   tampoco hace falta pushState -- es el estado inicial de la carga, no una
   navegación nueva que deba quedar en el historial. */
const GEO_SLUG_TO_ZONE = {
  "ile-de-france": "Île-de-France",
  "fuera-de-ile-de-france": "Francia fuera de IDF",
};
function applyInitialFiltersFromUrl() {
  const params = new URLSearchParams(location.search);
  const geoSlug = params.get("geo");
  if (geoSlug && GEO_SLUG_TO_ZONE[geoSlug]) STATE.geo = GEO_SLUG_TO_ZONE[geoSlug];
  const tema = params.get("tema");
  if (tema && CATEGORY_META[tema]) STATE.theme = tema;
  const buscar = params.get("buscar");
  if (buscar) STATE.search = buscar;
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
  DATA.events.forEach((ev) => {
    if (ev.geoZone) ev.geoZone = canonicalizeGeoZone(ev.geoZone);
    if (ev.cityName) ev.cityName = canonicalizeCityName(ev.cityName);
  });
  ACCOUNTS_BY_USER = {};
  (DATA.accounts || []).forEach((a) => { ACCOUNTS_BY_USER[a.username] = a; });
  markBetweennessDecile();
  buildTagFrMap();
  applyInitialFiltersFromUrl();
  render();
  // Link compartido (?evento=id): abrir ese evento directo al cargar. No
  // pushea historial de nuevo -- la URL ya viene así desde afuera.
  const sharedId = new URLSearchParams(location.search).get("evento");
  if (sharedId) {
    const ev = DATA.events.find((e) => e.id === sharedId);
    if (ev) openDetail(ev, { updateHistory: false });
  }
}
init();
