# Propuesta: Hub Cultural — sitio de descubrimiento de eventos culturales

**Fecha:** 2026-08-13
**Estado:** Propuesta v1 para discusión — no implementada. Reemplaza el dashboard Dash actual (`5_visualize_dashboard.py`), que Diego considera insatisfactorio (UX débil pese a datos ricos).
**Metodología:** research + propuesta generados por un agente sobre modelo Opus, con el schema real de `:Event`/`:Account` verificado contra el código del pipeline (no inventado). Mockup interactivo de la home construido aparte para revisar con Diego.

---

## 0. Diagnóstico previo (hallazgos en el código actual)

1. **El cuello de botella no son las 11 categorías, es el desbalance.** Gastronómico (235) + Institucional (142) = 57% de los 664 eventos. Una nav plana de 11 categorías con esa distribución produce 9 pestañas casi vacías y 2 llenas. El problema de arquitectura de información es de *equilibrio de inventario*, no de cantidad de etiquetas.
2. **`pages/eventos.py` / `dash_common.py:254` tiene `LIMIT 100` hardcodeado** en la query de eventos limpios, además del filtro `isPublicInvitation AND isUpcoming AND eventDate 2026-2027`. El dashboard viejo nunca mostró más de 100 eventos. Cualquier sistema de ranking necesita traer el universo completo de eventos válidos y rankear en la app, no truncar en Cypher por fecha.
3. **Dos capas geográficas de fiabilidad distinta**, hay que usarlas distinto:
   - `geoZone` (3 valores, vocabulario controlado, heredado de cuenta curada) → fiable pero grueso. Sirve de filtro primario.
   - `cityName` / `locationName` / `exactAddress` (texto libre del caption) → granular pero sucio. No sirve de filtro, sirve de display.
   - `:Location -[:LOCATED_IN]-> :Arrondissement -> :City -> :Country` (de `4_enrich_locations.py`) → el activo infrautilizado. Es el único geo granular limpio disponible. Debe alimentar el filtro de segundo nivel y el mapa.

---

## 1. Inspiración: 7 referencias y el patrón concreto a robar

- **DICE.fm** — "Mixtape" semanal: selección corta, fechada, editorializada, que se renueva sola. Cambia la promesa de "buscá entre 664 eventos" a "esto es lo que pasa esta semana".
- **Resident Advisor (RA Guide)** — lista cronológica agrupada por día con headers pegajosos, no un grid uniforme. Lección: nunca mostrar cero resultados sin salida — siempre ofrecer "relajá este filtro" con conteo.
- **L'Officiel des spectacles (offi.fr)** — 3.000 eventos/semana en 8 secciones fijas desde 1946. Pocas secciones estables + mucha densidad de listado escala mejor que muchas categorías. Navegación sección → día, no día → sección.
- **Eventbrite Discover** — barra de intención combinada que ancla la URL (`/d/{lugar}/{categoría}/`) + chips de fecha. Lugar es parte de la ruta, no un filtro escondido. El criterio de orden debe ser visible y cambiable en la lista.
- **Letterboxd** — transparencia algorítmica: muestra *por qué* aparece cada sugerencia. Traducción directa: cada tarjeta recomendada lleva un micro-chip explicativo ("Publicado por una cuenta puente entre comunidades"). Convierte la limitación (no hay personalización real) en virtud metodológica.
- **Spotify ("Made For You")** — estanterías horizontales con nombre editorial, cada una una consulta distinta con distinto peso de ranking. Le da superficie a las categorías minoritarias (una shelf de "Cine" con 21 eventos se ve rica; una pestaña con 21 se ve vacía).
- **Are.na / NTS Radio** — estética editorial sobria, tipografía sobre imagen. Permitir colecciones curadas transversales que crucen categorías ("Diáspora colombiana esta quincena", "Gratis y al aire libre") como queries guardadas, sin taxonomía nueva.

Fuentes completas al final del documento.

---

## 2. Arquitectura de información

### 2.1 Tres ejes ortogonales, siempre visibles (barra sticky)

```
[ 📍 DÓNDE ▾ ]  [ 📅 CUÁNDO ▾ ]  [ 🎭 QUÉ ▾ ]        [ ordenar: Recomendado ▾ ]
```

- **DÓNDE** (primario, nunca "todos" por defecto): nivel 1 = `geoZone` (Île-de-France / Francia fuera IDF / Fuera de Francia), tres botones. Nivel 2 (solo si IDF): arrondissements/comunas desde la jerarquía geocodificada, multi-select con conteo. Nivel 1 va en la URL (`/idf/esta-semana/gastronomia`).
- **CUÁNDO** (reusa `date_bucket_bounds()` tal cual): Hoy · Esta semana · Este mes · Próximos · Pasados. Default: Esta semana.
- **QUÉ**: ver 2.2.

### 2.2 Resolver las 11 categorías → 5 familias + subchips

| Familia | Categorías incluidas | Vol. aprox |
|---|---|---|
| Comer y beber | gastronomico | 235 |
| Escenario y pantalla | musical, escenico, audiovisual, festival | 97 |
| Ver arte | visual | 54 |
| Aprender | formacion, academico | 58 |
| Encontrarse | comunitario, institucional, politico | 180+ |

Al entrar en una familia aparecen los subchips con las 11 categorías originales y conteo, ocultando las de 0. **Riesgo a vigilar:** "Encontrarse" mezcla institucional (142, la etiqueta más vaga del pipeline) con comunitario y político — conviene revisar una muestra antes de fijar esto (ver pregunta 1).

**Filtros transversales** (panel "Más filtros"): Gratis (derivado de `priceRange` + `hasFreeEvents`, candidato a subir a chip primario), Identidad cultural (`culturalIdentity`), Tipo de arte (`artType`), Tipo de institución (`institutionType`).

### 2.3 Pantallas

1. **Home/Descubrir** — secuencia de estanterías: Hero (mayor score de la semana), `Destacados esta semana` (top 8, diversificados), `Gratis`, `Cerca de ti`, una shelf por familia, `Fuera del radar` (alto score/baja hotness — serendipia + argumento de tesis), `Voces de la red` (cuentas organizadoras top).
2. **Explorar** — vista densa, lista agrupada por día con headers pegajosos, toggle Lista/Grid/Mapa, orden visible.
3. **Mapa** — solo sobre `:Location` con lat/lon. Verificar primero cobertura real de geocoding antes de promoverlo a vista principal.
4. **Detalle de evento** — ver 2.5.
5. **Perfil de organizador** (`:Account`) — pantalla nueva: username/followers/verified, badge de rol (`tier`/`betweennessExact`), otros eventos, cuentas relacionadas por comunidad Leiden. Diferenciador frente a offi.fr.
6. **Metodología** — página estática: cómo se detectan eventos, qué significa "recomendado", límites conocidos. Sube credibilidad, sirve de anexo de la memoria.

### 2.4 Anatomía de la tarjeta

Imagen 16:9 (overlay/badge "Gratis" si aplica) → fecha (mono, color de familia) → título (2 líneas máx) → ubicación (`exactAddress` o `locationName` + arrondissement) → autor + badge de grafo. `hotnessScore`/`eventScore` nunca como número — se traducen a badges: `✦ Puente` (betweenness top decil), `↑ Resonando` (hotness percentil 80+), `◆ Confirmado x3` (postCount ≥ 3), `Gratis`. Máximo 2 badges por tarjeta.

### 2.5 Detalle de evento

Dos columnas: imagen + título + descripción + bloque "Qué sabemos" (fecha, ubicación, precio) + mini-mapa + CTA "Ver publicación original" (→ `sourcePostUrl`, honesto: es un descubridor, no una ticketera) | sidebar con tarjeta de organizador + tags heredados + "Por qué te lo mostramos". Abajo: "Eventos similares" por coseno sobre el `embedding` 384-dim ya existente (recomendación content-based real, sin costo extra). Pie colapsado con `confidence`/`postCount` y aviso de detección automática — obligatorio, dado que hay LLM en el camino.

---

## 3. Fórmula de ranking

**Principio declarado:** curación algorítmica contextual, no personalización — no hay cuentas de usuario. El ranking combina calidad de detección, autoridad de la fuente (grafo), resonancia social, proximidad temporal y contexto de sesión. Mismas condiciones → mismo resultado para todos.

Todas las señales se normalizan a percentil-rango `pctl(x)` sobre el universo de eventos publicables antes de combinar (igual que hacía el Cultural Relevance Score archivado — es la parte correcta de ese enfoque).

**Q — calidad de detección:** `0.70·eventScore + 0.30·confidence` (layer1Score no entra, ya correlacionado).

**A — autoridad de la fuente:** `0.40·pctl(pageRankExact) + 0.25·pctl(betweennessExact) + 0.20·pctl(manualFollowersCount ?? followersCount) + 0.10·pctl(participationCoef) + 0.05·(verified?1:0)`. PageRank pesa más porque mide reconocimiento dentro de la red cultural — la pregunta central de la tesis. Followers pesa poco a propósito: un consulado con 40k seguidores no es más relevante culturalmente que un colectivo con 3k.

**B — resonancia social:** `0.75·pctl(hotnessScore) + 0.25·min(postCount/3, 1)`.

**T — proximidad temporal** (escalones, no exponencial — más legible y defendible): pasado → 0; 0-2 días → 1.00; 3-7 → 0.85; 8-21 → 0.60; 22-60 → 0.35; >60 → 0.15.

**C — contexto de sesión:** sin señal, 0.5 neutro. Ver 3.5 para el mecanismo sin login.

**Score final:** `100 · (0.30·Q + 0.22·A + 0.18·B + 0.20·T + 0.10·C) · P`

Multiplicadores `P`: político ×0.55 (continúa la despriorización ya establecida en el pipeline) · confidence<0.50 ×0.80 · gratis ×1.05. ~~culturalIdentity no nulo ×1.05~~ — **eliminado (decisión 2026-08-13):** el proyecto ya no es exclusivamente sobre eventos colombianos (ver `CLAUDE.md`: "originally Colombian diaspora, now general cultural accounts in France too"), así que ser colombiano ya no debe influir el puntaje. `culturalIdentity` sigue existiendo como campo y como filtro que el usuario puede activar si quiere, pero no empuja el ranking por defecto.

**Nota sobre Leiden/comunidades:** de paso, confirmado en el código de `3_analyze_network.py` — la detección de comunidades (Leiden a las 3 resoluciones) es puramente topológica, se calcula solo a partir de las conexiones del grafo (MENTIONS/TAGS_USER/RELATED_TO). `culturalIdentity` nunca fue un insumo de ese análisis, así que no hay ningún lugar oculto donde la nacionalidad siga afectando el análisis de comunidades — si era eso lo que te preocupaba con el comentario sobre Leiden, ya está confirmado que no aplica. Si te referías a otra cosa, avisame.

**Diversificación obligatoria (greedy, sobre el top ~60):** máx. 2 eventos por `sourceAuthor`, máx. 3 por `category` en el top 12 de Home (sin esto gastronómico se come toda la portada, matemáticamente inevitable con 235/664), máx. 1 por `parentInstitution` en el top 6.

**Explicación por tarjeta** (componente de mayor contribución marginal): A → "Publicado por una cuenta central en la red cultural" · B → "Alta resonancia en Instagram esta semana" · T → "Ocurre en los próximos días" · postCount≥3 → "Mencionado por varias publicaciones" · C → "Coincide con lo que estuviste explorando".

### 3.5 "Recomendación" sin login — memoria de sesión

`localStorage` bajo `hcdu_prefs`, sin cookies de terceros, sin backend, sin PII: `{zone, arrondissements, catWeights, freeOnly, seen, updatedAt}`. `catWeights[c] += 1` al filtrar por esa categoría o abrir un evento suyo, `+= 2` al hacer clic en "ver publicación original". Decaimiento ×0.8/día, se descarta bajo 0.5. `C = 0.6·catMatch + 0.4·geoMatch`. `seen` (últimos 50) aplica ×0.85 para que el Home rote. Banner discreto la primera vez + botón de reset en footer — sin login, sin servidor, cumple RGPD trivialmente.

**Ruta a v2 (no construir ahora):** con cuentas reales, `catWeights` migra a perfil persistente, `C` sube de peso ~0.10→0.25, se habilita "seguir organizador" y filtrado colaborativo sobre el embedding de eventos. La fórmula no cambia de forma, solo se rellena mejor el componente C.

---

## 4. Estrategia de imágenes (v1)

**Hallazgo importante:** `source.unsplash.com` (la URL mágica `?keyword`) está muerta desde junio de 2024 — cualquier tutorial que la mencione está obsoleto. La API oficial exige registro, limita a 50 req/h en modo demo y exige atribución con enlace al fotógrafo — inviable como carga en caliente por tarjeta.

**Propuesta: pre-cosecha estática, asignación determinista.**

1. Script `tools/fetch_category_covers.py`: 6-8 imágenes por categoría (~88 total) desde Unsplash API oficial o Pexels (Pexels no exige atribución por foto, licencia más laxa), con keywords por categoría (ej. gastronomico → "latin food table, empanadas"; musical → "live music crowd, cumbia band"; etc. — lista completa de las 11 en el research original).
2. Guardar en `assets/covers/<category>/01.jpg…08.jpg` (1200px, WebP) + `credits.json`.
3. Asignación determinista: `índice = int(md5(event.id), 16) % n_imgs` — misma tarjeta siempre la misma foto, sin parpadeo.
4. Tratamiento visual unificador (duotono/overlay del color de familia + grano ligero) para que 88 fotos heterogéneas parezcan una sola dirección de arte.
5. Fallback digno sin imagen: bloque de color de familia + icono grande — usarlo deliberadamente en listas densas (patrón NTS/Are.na).
6. Puerta abierta a v2: campo `coverImage` nulo hoy; si el pipeline llega a guardar `displayUrl` de Apify, se usa esa y cae al genérico si falta. Ojo legal: imágenes de Instagram tienen derechos de terceros — solo hotlink con atribución visible, nunca copia local.

---

## 5. Notas técnicas

- Reemplazo de Dash recomendado: API de solo lectura (FastAPI sobre Neo4j) + front estático, o directamente un sitio estático (664 eventos caben en ~1-2MB de JSON) con filtrado 100% en cliente — sin servidor, sin latencia de Neo4j, desplegable en Netlify/Pages.
- Eliminar el `LIMIT 100` y el filtro duro de fecha 2026-2027 de `dash_common.py:254` — el rango temporal es responsabilidad de los filtros de UI, no de la query.
- El `embedding` viaja solo en backend (o índice pre-calculado de vecinos top-10 por evento), nunca al cliente.

---

## 6. Preguntas — decisiones de Diego (2026-08-13)

1. **¿5 familias o 4?** → **Revisar una muestra primero.** Pendiente: correr la query de la sección 6.4 y decidir con datos reales si `institucional` es re-clasificable antes de fijar la agrupación en familias.
2. **Filtro geográfico: ¿mapa o lista?** → **Mapa clickeable como selector primario**, no una lista de botones. La interacción es seleccionar una región (un arrondissement, o una comuna como Saint-Denis o Asnières-sur-Seine) tocándola directamente en el mapa, con el mismo efecto que apretar un botón — el mapa ES el control de filtro, no una vista aparte. Implica conseguir polígonos reales de arrondissements/comunas de Île-de-France (IGN / data.gouv.fr tiene los contornos oficiales en GeoJSON) y verificar antes la cobertura real de `lat`/`lon` en los eventos — si la cobertura es baja, los eventos sin geocoding necesitan un fallback visible (ej. agrupados en un chip "sin ubicar" fuera del mapa) en vez de desaparecer silenciosamente.
3. **¿"Gratis" chip primario?** → **Sí**, va en la barra principal junto a Dónde/Cuándo/Qué.
4. **Idioma de la interfaz** → **Bilingüe con selector** (español/francés). La UI (labels, nav, botones) se traduce; `title`/`description` de cada evento quedan en el idioma en que el LLM los redactó originalmente (mixto por diseño del pipeline).

5. **Peso visible del análisis de redes** → **Discreto (opción 1) para v1.** El análisis de grafos (PageRank, betweenness, comunidades) queda por debajo del capó: solo influye en el orden de los eventos y en badges ocasionales (ej. "✦ Puente"). Sin perfiles de organizador dedicados ni página de metodología por ahora — se puede agregar después si hace falta más adelante para la memoria.
6. **Bonus por `culturalIdentity`** → **Eliminado del ranking.** Ver sección 3.4 arriba — el proyecto ya no es solo sobre eventos colombianos, así que ya no hay ningún criterio de nacionalidad/identidad influyendo el puntaje por defecto. Queda únicamente como filtro opcional.

### 6.1 Reprioridad de familias — gastronomía pasa a secundaria (2026-08-13)

Diego aclaró que la curación manual de cuentas viene **evitando activamente gastronomía** para priorizar música, cine, fotografía y literatura — esto coincide con los 4 ejes temáticos originales del proyecto (`docs/objetivos.md`: Literatura, Fotografía, Cine, Teatro). Que gastronómico sea la categoría más grande (235/664, 35%) es un artefacto de qué cuentas son fáciles de scrapear/detectar, no una señal de qué le importa al proyecto.

**Decisión:** "Comer y beber" pasa a **estatus secundario** en la navegación — no desaparece (sigue siendo un filtro disponible), pero no ocupa un lugar prominente en el menú principal ni en el home por defecto. El resto de familias (Escenario y pantalla, Ver arte, Aprender, Encontrarse) quedan con estatus primario. Esto también implica revisar la fórmula de ranking: **el componente B (resonancia social)** tal como está no penaliza gastronómico, así que en la práctica seguiría dominando el "Destacados esta semana" por puro volumen — hace falta un ajuste explícito (ej. tope más chico en la diversificación por categoría para gastronómico específicamente, no solo el tope general de 3 por categoría) antes de implementar el ranking real.

**Hallazgo importante — hueco de taxonomía:** no existe ningún `type` dedicado a **literatura** en las 16 etiquetas de `_LABEL_META` (`4_enrich_events_extract.py`). Un lanzamiento de libro, una lectura de poesía o una charla literaria hoy caerían en `academico` (si tienen tono de charla) o simplemente no se detectarían como evento cultural distintivo. Dado que literatura es uno de los 4 ejes temáticos originales del proyecto, esto es más que un detalle de UI — es un hueco real del pipeline de extracción. Antes de asumir que "no hay eventos de literatura" en los datos, vale la pena correr esta query para ver si ya hay señal aprovechable vía `artType` de las cuentas curadas (aunque el evento en sí nunca se haya tipificado como tal):

```cypher
MATCH (a:Account)
WHERE a.artType IS NOT NULL AND toLower(a.artType) CONTAINS "literatura"
RETURN a.username, a.artType, a.manualDataCuratedAt
```

Si aparecen cuentas, sus eventos ya se pueden agrupar como "Literatura" en el dashboard vía `artType` heredado, sin tocar el pipeline de extracción. Si no aparece nada, hay que decidir si vale la pena agregar un `type` de literatura a `_LABEL_META` y reprocesar — eso es trabajo de pipeline, no de dashboard, pero condiciona qué tan bien el sitio nuevo puede realmente servir esa prioridad.

**Resultado (2026-08-13): sí hay señal real — pivote de arquitectura.** 18 cuentas curadas tienen "Literatura" en `artType` (Festival du Livre de Paris, CNL, Cité de la BD, Maison de la Poésie, Colombia Cuenta, etc.) — curación de calidad, no ruido. Esto lleva a un cambio de diseño: **el eje "QUÉ" primario pasa de basarse en `category` (generado por LLM, con el ruido de institucional ya documentado, dominado por gastronómico) a basarse en `artType` (dato curado a mano, alineado con los 4 ejes temáticos reales del proyecto)**. `category` queda como filtro secundario/fino, útil sobre todo para eventos de cuentas sin curar (sin `artType`).

**Problema técnico encontrado al analizar la distribución completa:** `artType` es texto libre con comas usadas tanto como separador de lista ("Literatura, Teatro, Danza, Música") como puntuación dentro de descripciones ("Multidisciplinario (música, historia, arqueología)") — un `split(artType, ',')` ingenuo rompe las descripciones parentéticas en fragmentos sin sentido. **Fix propuesto:** no separar por coma. En su lugar, definir una lista fija de temas conocidos (ver abajo) y usar `CONTAINS` insensible a mayúsculas por cada uno contra el `artType` completo — el mismo patrón de matching por palabra que ya se construyó para `geo_conflict()` en DD-041, reutilizable acá. Un evento puede matchear varios temas a la vez (multi-tag), y lo que no matchea ningún tema conocido cae a un residual ("Multidisciplinario"/sin tema claro) sin romper nada.

**Distribución real de temas limpios (cuentas curadas / eventos ya creados):**

| Tema | Cuentas | Eventos |
|---|---|---|
| Artes visuales | 21 | 25 |
| Música | 23 | 24 |
| Danza | 21 | 20 |
| Teatro | 15 | 16 |
| Literatura | 15 | 7 |
| Circo | 5 | 5 |
| Fotografía | 3 | 2 |
| Cine | 11 | 2 |
| Gastronomía (marcado explícitamente "No aplica" por el curador) | 3-4 | 4-6 |

**Dos hallazgos importantes:**
1. **Gastronomía se autoexcluye del sistema `artType`** — el curador la marcó como "No aplica — gastronomía" en vez de darle un tema de arte. Confirma independientemente la decisión de la sección 6.1 de bajarla a secundaria: ni siquiera en la curación manual se la trata como un eje temático del proyecto.
2. **Fotografía y Cine tienen volumen de eventos muy bajo hoy (2 cada uno)** pese a ser ejes prioritarios declarados — hay bastantes cuentas curadas (11 de Cine, 3 de Fotografía) pero pocos eventos detectados todavía. Un chip con 2 eventos se ve vacío/roto (lección de RA Guide en la sección 1). Vale la pena decidir si se muestran igual (crecen con el tiempo) o se pliegan dentro de un tema más amplio por ahora.

**Pendiente de decisión con Diego:** qué combinación final de estos 8 temas se vuelve chip primario del menú — en particular qué hacer con Artes visuales/Danza (altísimo volumen pero no nombrados explícitamente como prioridad) y con Fotografía/Cine (prioridad declarada, volumen bajo).

**Actualización (2026-08-13) — DD-042, ver `docs/decisions_es.md`:** revisando ejemplos reales de "Artes visuales" salió que `artType` heredado de cuenta es ruidoso a nivel de evento individual (cuentas-sede omnívoras como La Villette taggean TODOS sus eventos con su lista completa de disciplinas, sin importar el evento puntual — solo 40% de los eventos "Artes visuales" por herencia eran realmente `category='visual'`). Se decidió no usar `artType` como eje del menú. En su lugar: **el menú "QUÉ" se arma con `category` (juicio por evento, confiable) para Música/Cine/Artes visuales/Escena, y con un campo NUEVO `Event.eventArtTags`** (lista corta generada por el LLM por evento, cubre Literatura/Fotografía/Circo que `category` nunca tuvo) para lo que `category` no puede expresar. Implementado en `4_enrich_events_extract.py` — **solo aplica a eventos creados de ahora en adelante**, los 664 actuales no lo tienen todavía (ver DD-042 para el detalle de alcance y si vale la pena un backfill).

### 6.2 Mockups adicionales (2026-08-13)

Se construyeron mockups interactivos/visuales de las 3 pantallas restantes de la sección 2.3, coherentes con las decisiones tomadas hasta acá (sin bonus por `culturalIdentity`, sin perfiles explícitos more allá de lo básico, solo eventos geocodificados):
- **Detalle de evento** (2.5): bloque "Qué sabemos", CTA a la publicación original, "Por qué te lo mostramos", eventos similares, transparencia colapsada.
- **Explorar** (2.3): lista densa agrupada por día con headers, toggle Lista/Grid/Mapa, orden visible, salida sin dead-end cuando el filtro no tiene resultados.
- **Perfil de organizador** (2.3): métricas de centralidad en lenguaje simple ("Top 8% en centralidad"), sus próximos eventos, cuentas relacionadas por comunidad Leiden — versión ligera, consistente con la decisión "discreta" de la pregunta 5 (sin página de metodología todavía).

### 6.3 Cobertura real de coordenadas (2026-08-13)

Resultado de la query de la sección 6.2: **559 eventos válidos totales, 528 con nodo `:Location`, 478 con `lat`/`lon` reales (85.5%)**. Los 31 sin nodo `:Location` son eventos donde el LLM nunca encontró texto de ubicación en el caption (no llegan ni a intentar geocodificar). Los 50 que tienen `:Location` pero no coordenadas son direcciones que Nominatim no pudo resolver, o que todavía no pasaron por `4_enrich_locations.py` desde que se crearon — **no es necesariamente una cuestión de qué tan recientes son**; depende de si el texto de ubicación existía y si la fase de geocoding ya corrió sobre ellos desde entonces. Antes de fijar el número definitivo, vale la pena volver a correr `python 4_enrich_locations.py --dry-run` para ver cuántos de esos 81 son backlog pendiente (se resuelven solos al re-correr) vs. genuinamente no geocodificables.

**Decisión de producto (2026-08-13):** el mapa/lista de descubrimiento **solo muestra eventos con coordenadas reales** — nada de chip "otras ubicaciones" como fallback visible. Un evento sin dirección resoluble no entra al criterio central del sitio (geografía), así que queda fuera de la experiencia de descubrimiento por completo. Aclaración importante: esto es un filtro de **presentación**, no de borrado — el nodo `:Event` sigue existiendo en Neo4j para el resto del pipeline/análisis de la tesis, simplemente no aparece en este sitio. Con 85.5% de cobertura ya confirmada (y probablemente más alta tras re-correr el backlog), el mapa puede ser la vista geográfica principal sin necesidad de un plan B.

### 6.4 Resultado de la muestra institucional (2026-08-13) — analizado

Los 20 eventos `institucional` de mayor `hotnessScore` tienen `artType` **null en el 100% de los casos** — la señal que iba a usarse para re-clasificar no sirve acá, porque son cuentas puramente institucionales (consulados, embajadas, cuentas presidenciales) que nunca tuvieron `artType` en la curación manual. Hay que mirar el título/descripción a mano, no un campo estructurado.

Clasificando el contenido real de los 20 a mano salen 3 grupos:
- **Eventos públicos reales, bien categorizados (~8/20):** ceremonias, aperturas, homenajes con fecha/lugar concretos y asistencia pública real — ej. "Ceremonia bicentenario México-Francia", "Inauguración Copa Mundial Special Olympics", "Conmemoración Glorias Navales". Estos SÍ pertenecen a `institucional`.
- **Contenido que no es evento (~7/20):** entrevistas, podcasts, comunicados, anuncios de nombramientos — ej. "Podcast Latitud 4", "Presentación del nuevo pasaporte", "Encuentro del Embajador con CEO de Hermès" (reunión privada, no invitación pública). Esto es un **bug de la Capa 3 del pipeline de extracción** (el gate `is_public_invitation` debería haberlos filtrado y no lo hizo) — no es un problema de arquitectura del dashboard, es un problema de calidad de datos aguas arriba, separado de este proyecto.
- **Eventos mal categorizados, deberían ser otra categoría (~5/20):** dos instalaciones/exposiciones de Iván Argote (deberían ser `visual`), una gira de conciertos de la Camarata Lagar (debería ser `musical`), un menú de restaurante en Eibar sin ninguna relación con la diáspora colombiana (debería ser `gastronomico`, y además geográficamente no pertenece al proyecto), y un curso intensivo de francés (debería haber sido descartado por la regla explícita del prompt contra inscripciones a cursos regulares).

**Conclusión práctica:** `institucional` como categoría en sí es válida y vale la pena mantenerla separada (confirma que institucional NO es solo "ruido" — la mitad de la muestra son eventos públicos reales y distintos en tono de lo comunitario/festivo). Pero el 60% restante son dos problemas de calidad de datos separados del pipeline de extracción (gate de invitación pública con fugas + clasificación de `type` que a veces ignora el contenido real a favor de la cuenta que publica) — no es algo que el rediseño del dashboard pueda arreglar por sí solo. Recomiendo anotarlo como limitación conocida / trabajo futuro en la memoria, y NO bloquear el rediseño del dashboard por esto: la familia "Encontrarse" se separa como se decidió, con institucional aparte por volumen, sabiendo que ~30% de esos eventos tiene ruido de clasificación que se puede limpiar después con una revisión dirigida de Capa 3 (filtrar por `category='institucional' AND artType IS NULL` como primer corte, ya que ese combo identificó bien la zona de riesgo en esta muestra).

### 6.5 Selector geográfico — mapa esquemático (mockup arriba)

Construí una versión esquemática arriba para validar la interacción: cada arrondissement o comuna es un botón circular posicionado aproximadamente donde está en el mapa real (no a escala, sin polígonos oficiales todavía) — click selecciona, click de nuevo deselecciona, mismo comportamiento sea un arrondissement de París o una comuna de la petite couronne, tal como pediste. El contador por zona y el chip "Otras ubicaciones" (para eventos sin geocoding) están simulados con números de ejemplo.

Para la versión real hay dos caminos:
- **Preciso:** usar los contornos oficiales de arrondissements/communes de Île-de-France en GeoJSON (IGN "Admin Express" o data.gouv.fr, gratis, licencia abierta) renderizados con D3-geo o Leaflet — mapa geográficamente exacto, más trabajo de implementación.
- **Esquemático (como el mockup):** posiciones aproximadas a mano, sin geometría real — mucho más rápido de construir y mantener, pierde precisión geográfica pero cumple la función de filtro (la gente reconoce "Saint-Denis está al norte" sin necesitar el contorno exacto).

Mi recomendación: arrancar esquemático para validar que la interacción funciona bien en la práctica, migrar a GeoJSON real solo si el esquemático se siente insuficiente una vez que lo uses.

**Antes de construir cualquiera de las dos:** correr esta query para saber cuántos de los 664 eventos ya tienen coordenadas reales (necesario para decidir si el mapa puede ser la vista principal o necesita el fallback de "sin ubicar" de forma prominente):

```cypher
MATCH (e:Event)
WHERE NOT 'Rejected' IN labels(e)
OPTIONAL MATCH (e)-[:LOCATED_AT]->(l:Location)
RETURN count(e) AS total_eventos,
       count(CASE WHEN l IS NOT NULL THEN 1 END) AS con_nodo_location,
       count(CASE WHEN l.lat IS NOT NULL AND l.lon IS NOT NULL THEN 1 END) AS con_lat_lon
```

(Nota: `testing/check_geotag_coverage.py` mide algo distinto — geotag de Instagram y texto de `cityName`/`exactAddress`, no coordenadas reales de `:Location`. Esta query de arriba es la que responde la pregunta del mapa.)

### 6.6 Las dos preguntas que quedaron confusas — reformuladas en simple

**Pregunta 5 (peso del análisis de redes) en concreto:** ¿el trabajo de grafos (PageRank, comunidades, cuentas puente) queda por debajo del capó, solo influyendo en qué orden aparecen los eventos y con algún badge ocasional como "✦ Puente"? ¿O construimos pantallas dedicadas a mostrarlo — un perfil público por cada cuenta organizadora con sus métricas de centralidad, una sección "Voces de la red" en el home, y una página de metodología que explique el análisis? La primera opción es menos trabajo y un producto más limpio; la segunda es más vistosa para la tesis pero es más pantallas para construir.

**Pregunta 6 (bonus por identidad cultural) en concreto:** en la fórmula de ranking, un evento con `culturalIdentity` no vacío (ej. "Colombiana") sube 5% en el puntaje solo por tener ese dato, sin que el usuario haya pedido nada — es un pequeño empujón editorial a favor de contenido explícitamente identitario, coherente con que el proyecto es sobre la diáspora, pero es un sesgo real y declarado, no neutral. Tres opciones: (a) dejarlo como está y explicarlo en la página de metodología, (b) sacarlo del ranking y que `culturalIdentity` sea solo un filtro que el usuario activa si quiere, (c) subirlo más para que la identidad pese más en lo que se muestra por defecto.

### 6.7 Query para revisar la muestra de eventos institucionales (pregunta 1, ya usada)

Correr en tu terminal / Neo4j Browser y pegar el resultado:

```cypher
MATCH (e:Event {category: "institucional"})
WHERE NOT 'Rejected' IN labels(e)
RETURN e.title AS title, e.description AS description, e.sourceAuthor AS author,
       e.artType AS artType, e.locationName AS location, e.type AS type_original
ORDER BY e.hotnessScore DESC
LIMIT 20
```

`artType` es la pista clave: si varios eventos `institucional` tienen `artType` no nulo (ej. "Música"), es señal de que el pipeline los etiquetó `institucional` solo porque los publicó una cuenta institucional (consulado, embajada), no porque el evento en sí sea un acto administrativo — esos son candidatos claros a re-clasificar por `artType` heredado en vez de por `category`.

---

## Fuentes

- DICE — [How to find events you'll love](https://dicefm.zendesk.com/hc/en-gb/articles/22365220986897-How-to-find-events-you-ll-love-on-DICE) · [Fast Company, Most Innovative Companies 2024](https://www.fastcompany.com/91037567/dice-most-innovative-companies-2024)
- [RA Guide Redesign: Balancing Brutalist Aesthetics with Usability](https://medium.com/@emirceren/ra-guide-redesign-balancing-brutalist-aesthetics-with-usability-e6ae2c817969) · [Redesigning Resident Advisor App — UX Case Study](https://medium.com/@elen2698/redesigning-resident-advisor-app-a-ux-case-study-f8408a272934) · [RA Event Ranking 101](https://support.ra.co/article/268-event-ranking-101)
- [L'Officiel des spectacles](https://www.offi.fr/) · [Wikipedia](https://en.wikipedia.org/wiki/L%27Officiel_des_spectacles) · [Agenda Culturel Paris](https://75.agendaculturel.fr/)
- [Eventbrite Discover](https://www.eventbrite.com/d/ny--new-york/ux/) · [UX Analysis: Eventbrite](https://medium.com/@clinagyin_8435/eventbrite-a-ux-analysis-51e11649ad09)
- [UX/UI Case Study: A Human-Centered Evolution of Letterboxd](https://medium.com/@raquelcarmonareina/ux-ui-case-study-a-human-centered-evolution-of-letterboxd-f8436ddb13b5) · [The Inner Workings of Spotify's AI-Powered Recommendations](https://medium.com/beyond-the-build/the-inner-workings-of-spotifys-ai-powered-music-recommendations-how-spotify-shapes-your-playlist-a10a9148ee8d)
- [Filter UI and UX Design: Best Practices — UXPin](https://www.uxpin.com/studio/blog/filter-ui-and-ux/) · [Best Practices for Event Categories and Tags](https://theeventscalendar.com/knowledgebase/best-practices-for-using-event-categories-and-tags-for-filtering/)
- [RIP Unsplash Source](https://paul.af/rip-unsplash-source) · [Unsplash API Changelog](https://unsplash.com/documentation/changelog)
