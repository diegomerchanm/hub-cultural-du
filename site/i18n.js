const I18N = {
  es: {
    where: "Dónde", when: "Cuándo", free: "Gratis",
    categoryGroupLabel: "Categoría",
    sortLabel: "Ordenar", sortRecommended: "Recomendado", sortDate: "Fecha", sortPopularity: "Popularidad", sortDistance: "Cercanía",
    nearMe: "Cerca de mí",
    geoAll: "Toda Île-de-France y más",
    whenToday: "Hoy", whenWeek: "Esta semana", whenMonth: "Este mes", whenUpcoming: "Próximos", whenPast: "Pasados",
    themeAll: "Todo",
    heroEyebrow: "Destacado de la semana",
    shelfHighlights: "Destacados", shelfHighlightsSub: "Los mejor puntuados en tu zona y fecha elegidas",
    shelfFree: "Gratis", shelfFreeSub: "Entrada libre o gratuita",
    resultsAll: "Todos los eventos",
    resultsPast: "Eventos pasados",
    resultsCount: (n) => `${n} eventos`,
    emptyTitle: "No hay eventos con estos filtros",
    emptyBody: "Probá otra zona, otra fecha o quitá el filtro de tema.",
    loading: "Cargando eventos…",
    whatWeKnow: "Qué sabemos", price: "Precio", city: "Ciudad", address: "Dirección",
    mapTitle: "Mapa del lugar",
    viewOriginal: "Ver publicación original",
    similarEvents: "Eventos similares",
    whyShown: "Por qué te lo mostramos",
    detectionDetail: "Detalle de la detección",
    detectionText: (conf, posts) => `Confianza de detección ${conf} · confirmado por ${posts} publicación(es). Detectado automáticamente a partir de Instagram — verificá la fuente antes de asistir.`,
    reasonA: "Publicado por una cuenta central en la red cultural",
    reasonB: "Alta resonancia en Instagram esta semana",
    reasonT: "Ocurre en los próximos días",
    reasonPosts: "Mencionado por varias publicaciones",
    reasonC: "Coincide con lo que estuviste explorando",
    followers: "seguidores", frequency: "Frecuencia", freeEvents: "Eventos gratis",
    tabSummary: "Resumen", tabMoreInfo: "Más info",
    cultIdLabel: "Identidad", instTypeLabel: "Institución", parentInstitutionLabel: "Institución matriz",
    /* Categorías fijas del taxonomy del LLM (11, ver CATEGORY_META en app.js)
       — clave = ev.category tal cual viene de Neo4j (estable, no cambia con
       el idioma), valor = label visible. Agregado junto con geoLabels y
       months para que el switch ES/FR alcance también filtros y fechas, no
       solo el texto de interfaz (antes solo se traducía título/descripción
       del evento, DD-051). */
    categories: {
      gastronomico: "Gastronomía", institucional: "Institucional", visual: "Artes visuales",
      comunitario: "Comunidad", musical: "Música", formacion: "Talleres",
      audiovisual: "Cine", escenico: "Teatro y danza", festival: "Festivales",
      academico: "Charlas y conferencias", politico: "Cívico",
    },
    /* Zonas geo curadas a mano (load_manual_account_categorization.py,
       columna geoZone) — "No confirmado" es un cuarto valor ad-hoc que
       aparece en la planilla además de las tres zonas documentadas en
       CLAUDE.md. Cualquier valor nuevo que no esté acá cae sin traducir
       (mismo comportamiento de fallback que ya tenía GEO_LABEL). */
    geoLabels: {
      "Île-de-France": "Île-de-France", "Francia fuera de IDF": "Francia (fuera IDF)",
      "Fuera de Francia": "Fuera de Francia", "No confirmado": "No confirmado",
    },
    months: ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
  },
  fr: {
    where: "Où", when: "Quand", free: "Gratuit",
    categoryGroupLabel: "Catégorie",
    sortLabel: "Trier", sortRecommended: "Recommandé", sortDate: "Date", sortPopularity: "Popularité", sortDistance: "Proximité",
    nearMe: "Près de moi",
    geoAll: "Toute l'Île-de-France et plus",
    whenToday: "Aujourd'hui", whenWeek: "Cette semaine", whenMonth: "Ce mois-ci", whenUpcoming: "À venir", whenPast: "Passés",
    themeAll: "Tout",
    heroEyebrow: "À la une cette semaine",
    shelfHighlights: "À la une", shelfHighlightsSub: "Les mieux notés dans votre zone et date choisies",
    shelfFree: "Gratuit", shelfFreeSub: "Entrée libre ou gratuite",
    resultsAll: "Tous les événements",
    resultsPast: "Événements passés",
    resultsCount: (n) => `${n} événements`,
    emptyTitle: "Aucun événement avec ces filtres",
    emptyBody: "Essayez une autre zone, une autre date, ou retirez le filtre de thème.",
    loading: "Chargement des événements…",
    whatWeKnow: "Ce que l'on sait", price: "Prix", city: "Ville", address: "Adresse",
    mapTitle: "Carte du lieu",
    viewOriginal: "Voir la publication originale",
    similarEvents: "Événements similaires",
    whyShown: "Pourquoi on vous le montre",
    detectionDetail: "Détail de la détection",
    detectionText: (conf, posts) => `Confiance de détection ${conf} · confirmé par ${posts} publication(s). Détecté automatiquement à partir d'Instagram — vérifiez la source avant d'y assister.`,
    reasonA: "Publié par un compte central dans le réseau culturel",
    reasonB: "Forte résonance sur Instagram cette semaine",
    reasonT: "A lieu dans les prochains jours",
    reasonPosts: "Mentionné par plusieurs publications",
    reasonC: "Correspond à ce que vous explorez",
    followers: "abonnés", frequency: "Fréquence", freeEvents: "Événements gratuits",
    tabSummary: "Résumé", tabMoreInfo: "Plus d'infos",
    cultIdLabel: "Identité", instTypeLabel: "Institution", parentInstitutionLabel: "Institution mère",
    categories: {
      gastronomico: "Gastronomie", institucional: "Institutionnel", visual: "Arts visuels",
      comunitario: "Communauté", musical: "Musique", formacion: "Ateliers",
      audiovisual: "Cinéma", escenico: "Théâtre et danse", festival: "Festivals",
      academico: "Conférences et débats", politico: "Civique",
    },
    geoLabels: {
      "Île-de-France": "Île-de-France", "Francia fuera de IDF": "France (hors IDF)",
      "Fuera de Francia": "Hors de France", "No confirmado": "Non confirmé",
    },
    months: ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
  },
};

let CURRENT_LANG = "es";
function t(key, ...args) {
  const v = I18N[CURRENT_LANG][key];
  return typeof v === "function" ? v(...args) : v;
}
