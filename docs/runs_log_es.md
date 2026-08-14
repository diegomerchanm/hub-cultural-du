# Hub Cultural — Log de Ejecuciones

> Registro cronológico de cada ejecución significativa del pipeline.
> Complementa docs/decisions.md (el por qué) con el qué pasó y qué resultados obtuvimos.
> Base para el capítulo de resultados del mémoire.

---

## RUN-001 — Junio 2026 — Seed inicial
**Scripts:** 1_harvest_ig_profiles.py, 2_build_graph.py
**Input:** @consuladocolparis (único seed)
**Output:**
- 1 perfil scrapeado con datos completos
- ~80 nodos Account vacíos creados via relatedProfiles y menciones
- Costo: ~$0.0005 USD
**Resultado:** Grafo inicial con seed único operacional
**Lecciones:** El seed genera automáticamente nodos vacíos
de cuentas relacionadas sin costo adicional de scraping

---

## RUN-002 — Junio 2026 — Expansión BFS capa 1
**Scripts:** 1_harvest_ig_profiles.py, 2_build_graph.py
**Input:** ~80 cuentas vacías detectadas automáticamente por Neo4j
**Output:**
- 169 perfiles adicionales scrapeados
- 170 perfiles totales en data_raw/
- 4,637 nodos Account en Neo4j
- 107 cuentas :Public, 63 cuentas :Private
- Costo: $0.0026 USD total acumulado
**Resultado:** Corpus inicial de 170 perfiles operacional
**Lecciones:**
- Costo extremadamente bajo ($0.0005/perfil)
- Las 4,467 cuentas vacías restantes son lista de prospección orgánica
- Origen de cuentas: 1,530 comentarios, 1,001 relatedProfiles,
  906 menciones, 740 etiquetas, 290 desconocido

---

## RUN-003 — Julio 2026 — Posts culturales priorizados
**Scripts:** 1_harvest_ig_posts.py, 2_build_graph.py
**Input:** 7 cuentas culturales seleccionadas manualmente:
  dichaparis, el_man_de_los_chorizos, elcafetal.paris,
  ivan_argote, calisabor_salsa_calena,
  alianzafrancesademedellin, educulturaco
**Output:**
- 333 posts scrapeados (50 por cuenta, 33 calisabor)
- 1,190 posts totales en Neo4j
- Costo: $0.82 USD
**Resultado:** Posts profundos disponibles para pipeline NLP
**Lecciones:** El costo de posts (~$0.12/cuenta) es 240x
mayor que perfiles (~$0.0005) — necesita filtro NLP previo

---

## RUN-004 — Julio 2026 — NLP enriquecimiento de nodos
**Scripts:** 4_enrich_nodes_nlp.py
**Input:** 123 bios de Account + 1,317 captions de Post
**Output:**
- 123 bios enriquecidas con idioma, entidades NER, keywords
- 1,317 captions enriquecidos
- Distribución idiomas bios: es=82, unknown=18, en=14, fr=9
**Resultado:** Nodos enriquecidos con semántica NLP
**Lecciones:**
- spaCy no extrae DATE en bios (esperado — bios son estáticas)
- Filtros post-NER necesarios: URLs, saltos de línea, >60 chars
- MISC de spaCy muy ruidoso en textos de Instagram — eliminado

---

## RUN-005 — Julio 2026 — Detección de eventos v1 (fallida)
**Scripts:** 4_enrich_events_extract.py (versión original)
**Input:** 1,085 posts
**Output:**
- Tasa de detección: ~0.7% (7 eventos de 1,085 posts)
- Tiempo: ~5 horas en CPU i5
**Resultado:** FALLIDO — pipeline inutilizable
**Lecciones:**
- Bug crítico 1: modelo NLI monolingüe inglés
  (cross-encoder/nli-MiniLM2-L6-H768) sobre textos ES/FR
- Bug crítico 2: spaCy ES/FR no tiene label DATE
- Bug crítico 3: make_event_id() con fecha no normalizada
- Todos identificados por revisión Fable

---

## RUN-006 — Julio 2026 — Detección de eventos v2 (corregida)
**Scripts:** 4_enrich_events_extract.py (post-fixes Fable)
**Input:** 1,289 posts (después de reset de sentinel)
**Output:**
- Tasa de detección: 74% (756 eventos de 1,289 posts)
- Tiempo: 18 minutos (vs 5 horas anterior)
- 494 eventos creados, 262 enriquecidos
- Distribución: 447 gastronómico, 108 institucional,
  57 visual, 41 formación, 32 comunitario, 24 festival,
  22 musical, 3 escénico, 3 audiovisual, 1 político
**Resultado:** Pipeline NLP operacional
**Lecciones:**
- Fix 1: mDeBERTa-v3-base-xnli-multilingual-nli-2mil7
- Fix 2: dateparser para extracción de fechas
- Fix 3: máximo de similitud vs promedio en Capa 1
- Fix 4: modelo ligero MiniLMv2 como default en Capa 2b
- Speedup 10x via batch inference
- 259 gastronómicos sospechosos → revisión manual →
  muchos son posts de menú, no eventos reales

---

## RUN-007 — Julio 2026 — Resolución de duplicados
**Scripts:** 4_enrich_events_resolve.py
**Input:** 504 eventos (después de limpieza manual)
**Output:**
- 14 pares duplicados fusionados
- 220 relaciones redirigidas
- 490 eventos finales
- Hotness promedio: 2.900
- Max postCount: 11
**Resultado:** Eventos deduplicados en Neo4j
**Lecciones:**
- Normalización de location crítica: París/Paris/paris
  deben ser el mismo grupo (fix con unidecode)
- Threshold 0.75 correcto para similitud semántica
- Triple criterio necesario: location + fecha ±3d + similitud

---

## RUN-008 — Julio 2026 — Geocodificación de locations
**Scripts:** 4_enrich_locations.py
**Input:** 550 nodos Location en Neo4j
**Output:**
- 550/550 locations geocodificadas (100%)
- Distribución geográfica:
  Francia: 245, Colombia: 93, España: 44,
  Brasil: 19, otros: 149
- Rate limit Nominatim: 1 req/s → ~10 minutos total
**Resultado:** Red transnacional en 8+ países confirmada
**Lecciones:**
- La red no es solo París — es transnacional
- Consistente con teoría de simultaneidad transnacional
  (Vertovec, 2009)
- Algunos falsos positivos: "Festival De" → Alemania,
  "Este Domingo" → República Dominicana

---

## RUN-009 — Julio 2026 — Análisis de red v1
**Scripts:** 3_analyze_network.py export/analyze/writeback
**Input:** 4,637 nodos, 2,939 aristas sociales,
          1,300 aristas algorítmicas
**Output:**
- 61 comunidades Leiden (modularidad 0.88)
- E-I Index social global: -0.2149
  · individual: -0.2020 (localista)
  · institucional_cultural: +0.8027 (puente)
  · comercial: +0.9059 (puente fuerte)
  · medio: +1.0000 (conector puro)
- Betweenness @consuladocolparis: 13,108
- Densidad del grafo: 2,047 nodos / 2,300 aristas (ratio 1.1)
**Resultado:** Análisis de red v1 completo con hallazgos
sociológicos significativos
**Lecciones:**
- Densidad demasiado baja para rankings discriminativos
- Bug identificado: proyección GDS anterior solo veía
  RELATED_TO, no MENTIONS/TAGS_USER — corregido por Fable
- Los negocios latinos son los puentes reales de la diáspora
  (E-I comercial = +0.9059) — hallazgo citable

---

## RUN-010 — Julio 2026 — Análisis de red con tiers
**Scripts:** 3_analyze_network.py export/analyze/writeback
**Input:** 4,637 nodos con tier asignado
  (27 primary, 9 secondary, 17 excluded, 4,584 unknown)
**Output:**
- Mismos algoritmos sobre grafo completo
- Filtro tier solo en reporte final
- Top primary por PageRank disponible para semillas V2
**Resultado:** Rankings limpios sin ruido político/comercial
**Lecciones:**
- 99% del grafo es unknown — baja densidad confirmada
- Necesita más ciclos BFS para ser discriminativo
- Sistema de tiers correcto pero corpus insuficiente

---

## RUN-011 — Julio 2026 — Primer scrapeo de seeds V2
**Scripts:** 1_harvest_ig_profiles.py --seeds config/seeds_v2.json, 
2_build_graph.py
**Input:** 25 seeds V2 (18 bloque A consulados/embajadas + 7 bloque B 
instituciones culturales, DD-022). 2 ya tenían perfil local 
(consuladocolparis, embajadacolfra).
**Output:**
- 23 perfiles nuevos scrapeados, 0 fallos
- 193 perfiles totales en data_raw/ (170 de V1 + 23 nuevos)
- 5,433 nodos :Account en Neo4j (+796 vs. los 4,637 de RUN-002/010)
- De esos +796: 23 son perfiles completos (las seeds), ~773 son nodos 
  nuevos descubiertos vía relatedProfiles/taggedUsers/coauthorProducers/
  mentions/comentarios de las 23 cuentas institucionales
- Costo real: $0.06 USD (calibración previa había estimado $1.34-$3.36 
  — sobreestimación corregida)
- Error transitorio de conexión Neo4j durante 2_build_graph.py, resuelto 
  automáticamente por retry — no afectó el resultado final
**Resultado:** Expansión V2 operacional, lista para clasificador NLP
**Lecciones:**
- El patrón BFS de RUN-001/002 escala bien: 23 seeds institucionales 
  generaron ~33 nodos nuevos promedio cada una (menos que los ~80 que 
  generó @consuladocolparis sola en RUN-001 — instituciones parecen tener 
  relatedProfiles/engagement más acotado que una cuenta comunitaria activa)
- Hallazgo de heterogeneidad de datos: 2,665 de los nodos nuevos tienen 
  fullName (relatedProfiles/taggedUsers), 2,575 son solo username 
  (mentions/comentarios) — ver DD-027

---

## RUN-012 — Julio 2026 — Segundo scrapeo dirigido por clasificador NLP
**Scripts:** 1_harvest_account_classifier.py --diagnose, 
1_harvest_ig_profiles.py --from-classifier, 2_build_graph.py
**Input:** account_scores.csv tras la expansión de RUN-011 (5,433 cuentas 
evaluadas, umbral sin modificar: THRESHOLD_ORG=0.60, THRESHOLD_PERSON=0.75)
**Output:**
- keep=True: 70 cuentas (1.3%) · Roles: context=5345, target=63, seed_source=25
- data_completeness (DD-027): promedio=0.52, bimodal — 2565 en banda 0-0.33, 
  11 en banda 0.34-0.66, 2857 en banda 0.67-1.0
- De las 63 target, 57 ya tenían perfil completo (V1) — el chequeo 
  incremental las saltó automáticamente
- 6 perfiles genuinamente nuevos scrapeados: festivaldautomne, 
  domingo_pal_bailador_paris, parisglobefestival, ruedadecumbia.paris, 
  cinema.lemelies.montreuil, parislete — todos descubiertos vía 
  relatedProfiles/taggedUsers de las seeds institucionales V2 
  (data_completeness=0.8 antes del scrapeo)
- 199 perfiles totales en data_raw/ (136 públicos, 63 privados)
- Costo: $0.00 USD (por debajo del redondeo mostrado por el script)
- Ninguna de las 6 cuentas nuevas trajo latestPosts — pendiente evaluar si 
  vale la pena 1_harvest_ig_posts.py sobre ellas
**Resultado:** Ciclo completo de dos fases (BFS + filtro NLP + scrapeo 
dirigido) operacional y validado de punta a punta sobre seeds V2
**Lecciones:**
- La estrategia de dos pasadas resultó altamente eficiente: de 796 nodos 
  nuevos generados en RUN-011, solo 6 requirieron inversión real de scraping 
  — el resto o ya eran conocidos (57) o no pasaron el filtro NLP (733)
- Umbral original del clasificador (0.60/0.75) se mantuvo sin ajuste — 
  validado como funcional por Diego tras revisar el --diagnose
- data_completeness (DD-027) sigue siendo solo diagnóstico; su naturaleza 
  bimodal sugiere que un tratamiento categórico (full/partial/bare) sería 
  tan informativo como uno continuo, dada la escasez de casos intermedios

---

## RUN-013 — Julio 2026 — Pérdida de datos por versión de código desactualizada (incidente)
**Scripts:** 1_harvest_ig_posts.py
**Input:** 4 cuentas con historial de RUN-003 (dichaparis, elcafetal.paris,
educulturaco, ivan_argote) — ejecutadas a las 09:16-09:25 UTC del
2026-07-13, 14-23 minutos ANTES de que el commit e2ae5fc (DD-029, merge
+ ventana dinámica) se aplicara.
**Output:**
- El código que corrió era el de 8f58d50 (DD-028) — overwrite directo,
  sin merge_and_cap.
- dichaparis: overwrite total, quedaron solo 2 posts nuevos (perdió ~48
  históricos de RUN-003).
- elcafetal.paris, educulturaco, ivan_argote: overwrite con el
  placeholder de error "no_items" de Apify (bug adicional — el chequeo
  "if not dataset_items" no detectaba ese caso, ver DD-030), perdiendo
  el 100% de su historial de RUN-003.
- Estado corrupto ingestado a Neo4j vía 2_build_graph.py antes de
  detectarse.
- Recuperación intentada vía API de Apify: imposible — los datasets de
  RUN-003 (2026-07-01) ya expiraron. La API solo retiene las últimas ~83
  corridas (rango disponible: 2026-07-12 a 2026-07-13). Pérdida
  permanente confirmada.
**Resultado:** INCIDENTE — pérdida de datos parcial, causa raíz
identificada y corregida.
**Lecciones:**
- Causa raíz real: desfase temporal entre la ejecución del script y el
  commit del fix que se creía ya aplicado — no un bug en merge_and_cap
  ni en days_to_fetch (ambos funcionan correctamente, confirmado vía
  API de Apify comparando timestamps de commit vs. timestamps de runs).
- Bug secundario real y confirmado: el placeholder de error
  "{'error': 'no_items', ...}" de apify/instagram-post-scraper es una
  lista de 1 elemento — truthy en Python — así que "if not dataset_items"
  nunca lo detectaba, en ninguna versión del script hasta este fix (DD-030).
- data_raw/ está gitignored — no hay respaldo automático de datos
  crudos. Perder un archivo local es perder el dato, salvo que Apify
  todavía retenga el dataset original (verificado: no en este caso).
- Protocolo a futuro: confirmar que un commit de fix está aplicado
  ANTES de correr el script que depende de él, no asumir que "ya está
  comiteado" sin verificar con git status/git log.

---

## RUN-014 — Julio 2026 — Validación del fix geo_hard_signals (DD-031/DD-032)
**Scripts:** 1_harvest_account_classifier.py --diagnose
**Input:** data_processed/account_scores.csv (5,433 cuentas) tras los
commits 9180ad4 (bbox lat/lon, DD-031) y fe65863 (AF_SATELLITE, DD-032)
**Output:**
- 7/7 cuentas objetivo pasaron a keep=False:
  alianzafrancesademedellin (username:AF_satellite:medellin),
  alianzafrancesacali (addr:OUTSIDE_FR:3.44,-76.52 + AF_satellite:cali),
  alianza_francesa_de_pereira (addr:OUTSIDE_FR:4.81,-75.70 + AF_satellite:pereira),
  unadunioneuropea (addr:OUTSIDE_FR:40.43,-3.67),
  williamsanchezinmobiliaria (addr:OUTSIDE_FR:39.99,-0.05),
  embcolghana (addr:OUTSIDE_FR:5.61,-0.18),
  remaxmariavillasmil02 (addr:OUTSIDE_FR:7.77,-72.21)
- keep=True: 62 → 61 (exactamente la cuenta esperada, sin más)
- Sin daño colateral: calisabor_salsa_calena y francy_barahona_calisabor
  (ambas con "cali" en el username, ambas radicadas en París) verificadas
  intactas en keep=True con geo≥0.99
- Sin red ni Neo4j necesarios — validación completa offline sobre CSV
**Resultado:** Fix validado end-to-end
**Lecciones:**
- businessAddress con lat/lon es la señal geográfica más robusta
  disponible; generaliza a cualquier país sin mantenimiento de listas
- El residual post-bbox (alianzafrancesademedellin, sin lat/lon, bio
  sobre "Francia" como tema) requirió una regla acotada al patrón de
  nombre (AF_SATELLITE), no una regla general de username con ciudad LatAm
- _tokens_in() usa word boundary — no funciona para tokens embebidos en
  usernames sin separadores; usar substring (_norm(tok) in _norm(username))
  en contextos donde ya hay gating por otro patrón fuerte

---

## RUN-015 — 2026-08-11 — Revalidación posts 1-100 tras rediseño de 3 capas (DD del rediseño de eventos)
**Scripts:** 4_enrich_events_extract.py --dry-run --max-posts 100 --diag-csv eval_100_v2.csv
**Input:** los mismos 100 posts de la evaluación original (eval_100.csv),
re-procesados con el pipeline ya rediseñado (gating por Capa 3, sin Capa
2b, tipificación por LLM) — LLM_PROVIDER=cerebras
**Output:**
- 12 descartados Capa 1, 88 candidatos Capa 2, 40 rechazados por LLM,
  46 eventos detectados
- Comparación contra la clasificación independiente de Claude
  (claude_labels.json, mismos post_id): **92/100 de acuerdo** (vs. 58%
  antes del rediseño, vs. 86% proyectado por simulación)
- Bug encontrado y corregido en la corrida siguiente (RUN-016): `math
  domain error` en `compute_hotness()` por `likesCount=-1` (cuentas con
  conteo de likes oculto en Instagram)
**Resultado:** rediseño validado end-to-end sobre datos reales, mejora
confirmada por encima de lo simulado
**Lecciones:**
- Los 8 desacuerdos restantes se explican por: descartes de Capa 1 (2),
  rechazos del gate de Capa 3 en casos límite (3), y 1 caso inverso
  (LLM aceptó un post informativo/institucional como evento público)

---

## RUN-016 — 2026-08-11 — Clasificación batch 2 (posts 101-200) + fix de compute_hotness
**Scripts:** 4_enrich_events_extract.py --dry-run --skip 100 --max-posts 100 --diag-csv eval_101_200.csv
**Input:** 100 posts nuevos (nunca antes clasificados por Claude ni usados
para ajustar el pipeline) — LLM_PROVIDER=cerebras
**Incidente:** primer intento crasheó con `ValueError: math domain error`
en `compute_hotness()` — `math.log1p(-1)` sobre `likesCount=-1` (Instagram
permite ocultar el conteo de likes). Corregido clampeando likes/comments a
`max(0, x)` antes del log1p; recompilado y re-ejecutado sin problema.
**Output:**
- 16 descartados Capa 1, 84 candidatos Capa 2, 46 rechazados por LLM,
  36 eventos detectados
- Clasificación independiente de Claude: 41 SI / 59 NO
- Comparación contra el script: **87/100 de acuerdo** — más bajo que el
  92% de RUN-015 porque estos posts nunca se usaron para ajustar nada
  (medida más honesta de generalización)
- `claude_labels.json` re-indexado de índice (0-99) a `post_id` y
  fusionado con el batch 2 (197 entradas únicas — 3 posts co-publicados
  por dos cuentas comparten `post_id` entre los primeros 100)
**Resultado:** confirma que la pipeline generaliza razonablemente bien a
datos nunca vistos, con patrones de error identificables
**Lecciones:**
- 3/13 desacuerdos: Capa 1 sigue descartando anuncios de tono sobrio
- 6/13: gate de Capa 3 rechaza versiones "cortas" de un evento ya
  anunciado en otro post más detallado de la misma cuenta
- 4/13 (hallazgo nuevo): el script le asigna fecha exacta a posts que
  solo mencionaban un mes suelto ("este marzo") — root cause identificado
  en `dateparser` + `PREFER_DAY_OF_MONTH='first'`, no en el LLM → fix en
  DD-034

---

## RUN-017 — 2026-08-11 — Batch 3 parcial (posts 201-300), corriendo en lotes de 50 por conectividad inestable
**Scripts:** 4_enrich_events_extract.py --dry-run --skip {200,250} --max-posts 50 --diag-csv eval_{201_250,251_300}.csv
**Input:** 100 posts nuevos (201-300), continuando el muestreo hacia los
500 acordados para la eventual revisión de embeddings de Capa 1
**Output:**
- eval_201_250.csv: 4 descartados Capa 1, 46 candidatos Capa 2, 27
  rechazados por LLM, 19 eventos detectados
- eval_251_300.csv: 4 descartados Capa 1, 46 candidatos Capa 2, 20
  rechazados por LLM, 26 eventos detectados
- En eval_251_300.csv, Groq fue el proveedor inicial y falló por
  conectividad real (`ReadTimeout`/`ConnectionError`, no 429) — el
  failover a Cerebras funcionó correctamente en <90s (ver DD-033)
**Resultado:** datos crudos listos; clasificación independiente de Claude
y comparación contra el script — completada en RUN-018 (ver abajo)
**Lecciones:** correr en lotes de 50 (en vez de 100 o 300) resultó
razonable dado el internet inestable del usuario — cada lote pierde como
máximo ~10-12 min si el proceso se cae, contra ~70 min de una corrida
única de 300

**Comparación (completada tras la interrupción del sandbox):**
- eval_201_250.csv: **45/49 de acuerdo (91.8%)**
- eval_251_300.csv: **43/50 de acuerdo (86.0%)**
- `claude_labels.json`: 197 → 296 entradas únicas (99 nuevas, 1 duplicado
  de post_id co-publicado colapsado)

---

## RUN-018 — 2026-08-12 — Batch 3 continuación (posts 301-400), en lotes de 50
**Scripts:** 4_enrich_events_extract.py --dry-run --skip {300,350} --max-posts 50 --diag-csv eval_{301_350,351_400}.csv
**Input:** 100 posts nuevos (301-400), corridos por Diego localmente
mientras el sandbox de Claude estaba caído; clasificación independiente
hecha por Claude leyendo los CSV directamente (sin bash) para las primeras
filas, y con pandas una vez el sandbox volvió.
**Output:**
- `claude_labels.json`: 296 → 396 entradas únicas (100 nuevas, sin
  colisiones de post_id esta vez)
- Comparación contra el script:
  - eval_301_350.csv + eval_351_400.csv combinados: **88/100 de acuerdo
    (88.0%)**
- Clasificación independiente de Claude en este batch: 38 SI / 62 NO
**Resultado:** cuarto batch de 100 clasificado y fusionado; van 396 posts
únicos con etiqueta independiente de Claude acumulados hacia los 500
acordados
**Lecciones — patrones de desacuerdo (12/100):**
- 2/12: Capa 1 sigue descartando posts con fecha/hora explícita cuando el
  tono es sobrio/institucional (mesa redonda académica, coloquio) — mismo
  patrón que RUN-016
- 2/12: Capa 3 rechaza eventos con solo marcador de fecha relativo
  ("aujourd'hui... à 16h", "ce week-end") sin fecha calendario explícita
  — patrón nuevo, posible sesgo del LLM hacia fechas absolutas
- 1/12: Capa 3 acepta apertura de inscripciones a curso de idiomas como
  evento (falso positivo — es admisión/inscripción, no evento cultural
  con asistencia, ver criterio ya establecido en RUN-016/DD-034)
- 4/12: Capa 3 acepta posts sin ninguna fecha explícita en el texto
  (listados de repertorio con "Pass 104infini", apertura de venta de
  entradas sin fecha del evento, promoción de partido sin fecha) —
  sugiere que el LLM a veces infiere una fecha plausible del contexto
  (temporada, calendario del Mundial) en vez de exigir que el texto la
  contenga
- 3/12: desacuerdo metodológico, no error del script — Claude clasificó
  como SI exposiciones "en curso hasta el [fecha]" con fecha de cierre
  explícita, aunque el texto sea mayormente un recap en pasado de la
  inauguración; el script las rechazó. Pendiente decidir si esta
  extensión del criterio (evento continuo con fecha de cierre cuenta como
  SI) debe formalizarse o revertirse — ver nota en el criterio de
  clasificación

---

## RUN-019 — 2026-08-12 — Batch 3 final (posts 401-500), cierre de la muestra de 500

**Scripts:** 4_enrich_events_extract.py --dry-run --skip 400 --max-posts 100 --diag-csv eval_401_500.csv
**Input:** 100 posts nuevos (401-500, un solo archivo esta vez en vez de 2×50 —
la conectividad ya no era un problema), corridos por Diego. 99 post_id
únicos (1 duplicado co-publicado). Este tramo introdujo por primera vez
cuentas de un clúster nuevo: sedes de Alianza Francesa en Colombia
(Pereira, Cali, Manizales), `aecidcolombia`, `ueencolombia`, `culturespaces`,
`elcafelatino`, `mep.paris`, `miraartfair`, `ircam_paris`, `pac_colibri`.
**Output:**
- `claude_labels.json`: 396 → 495 entradas únicas (99 nuevas)
- Comparación contra el script: **83/99 de acuerdo (83.8%)** — el más bajo
  de los 4 batches de 100 (92%, 87%, 88%, 83.8%)
- Clasificación independiente de Claude en este batch: 40 SI / 59 NO
- **Total acumulado: 495 posts únicos clasificados independientemente por
  Claude — la muestra de 500 acordada para retunear Capa 1 queda
  esencialmente completa** (495 en vez de 500 exactos por los post_id
  co-publicados que colapsan en cada batch, mismo patrón que en 1+2)
**Resultado:** batch 3 completo (201-500). Cuarto y último tramo de 100
clasificado y fusionado.
**Lecciones — la caída de acuerdo (88%→83.8%) se explica por un clúster de
cuentas nuevo, no por regresión de la pipeline:**
- 6/16 desacuerdos: Capa 3 acepta como evento contenido que es en realidad
  promoción de curso/admisión o promoción genérica de temporada sin fecha
  puntual — concentrado casi todo en las sedes de Alianza Francesa
  colombianas (Pereira x2, Manizales x1) y en `culturespaces`/`tamalesenparis`
  (x3). Mismo patrón que DD-034/RUN-018 pero mucho más frecuente en este
  clúster — las Alianzas Francesas publican con una estructura de post
  (fecha + horario + lugar) casi idéntica a un evento cultural real, aunque
  el contenido sea "inicio de clases" o "test de nivel gratis"
- 3/16: Capa 3 sigue rechazando marcadores de fecha relativos ("dans une
  semaine", "cet après-midi", "c'est aujourd'hui") — mismo patrón ya
  detectado en RUN-018, confirma que es sistemático y no un caso aislado
- 1/16: script acepta un listado de repertorio (Pass 104infini) sin fecha
  — mismo patrón recurrente de 104paris ya visto en RUN-018
- 1/16: Capa 3 rechaza una transmisión de partido del Mundial con fecha y
  hora explícitas (Alianza Francesa de Manizales) — falso negativo aislado
- 4/16 (3 casos + 1 nuevo): variantes del debate metodológico de DD-035
  (exposición/temporada "en curso" o "que reanuda" con fecha explícita,
  aunque el post sea mayormente recap o promocional) — cierre de festival
  de Annecy con fecha de ceremonia explícita, reanudación de temporada de
  la Maison de la Poésie el 4 de septiembre, reanudación de ciclo de cine
  en la Cinemateca — todos casos límite de la misma naturaleza, refuerza
  que vale la pena resolver DD-035 antes de usar este dataset para
  retunear Capa 1
**Siguiente paso:** con los 495 posts completos, hacer un análisis
agregado de los ~50-55 desacuerdos totales de los 4 batches antes de tocar
Capa 1 — en particular decidir DD-035 primero, porque ese patrón por sí
solo explica un puñado de "falsos negativos" del script que en realidad
son ambigüedad de criterio, no error de modelo.

---

---

## RUN-020 — Primera corrida real (--dry-run) de los cambios DD-036, 150 posts de la última tanda

**Fecha:** 2026-08-12
**Alcance:** `python 4_enrich_events_extract.py --dry-run --accounts <43 cuentas curadas de la última tanda scrapeada> --max-posts 150 --diag-csv data_processed/eval_ultima_tanda_150.csv`. Primera vez que DD-036 corre contra datos reales en vez de una simulación offline.
**Resultado agregado del script:** 150 posts procesados, 29 descartados en Capa 1, 1 descartado en Capa 2, 58 sin fecha en texto (LLM omitido — gate DD-036), 25 rechazados por Capa 3, 37 EVENTO. Llamadas reales a Capa 3: 62 de 120 que se habrían hecho sin el gate previo (**48.3% de ahorro**, mejor que el 40.7% estimado offline).
**Comparación ciega Claude-vs-script (los 150, no solo los EVENTO):** 96.7% de acuerdo (145/150) — el mejor resultado de toda la sesión, por encima del rango 83-92% de los batches 1-3. Precisión 97.3% (36/37), recall ~90% (36/40).
**Desacuerdos (5, ver DD-037 para el detalle):**
- 1 falso positivo con causa de código confirmada: notación de temporada "26/27" leída como fecha DD/MM (`@theatrechatelet`)
- 3 falsos negativos con causa de código confirmada: ventana de 600 caracteres en `extract_dates()` deja fuera fechas que caen más adelante en captions largos, más un problema de separador (`.` no reconocido) — `@pointephemere`, `@academiamaritzaarizala`, `@saveurs_mexique`
- 1 caso sin causa de código clara: dos posts casi idénticos de `@mestizos.folklorecolombien` sobre el mismo evento, uno aceptado y otro rechazado por Capa 3 — variabilidad del LLM, a vigilar, no un patrón confirmado
- 1 caso aceptado como acuerdo operativo, no como error: `@oneculture.fr` anunciando un evento del mismo día ("Aujourd'hui 11h-22h") fue rechazado por el LLM — consistente con la decisión explícita de Diego de no tratar eventos del mismo día como relevantes dado el cadence del script
**Confirmaciones positivas de DD-036 en vivo:** la instrucción de exclusión de inscripciones/admisiones (cambio 3) funcionó — los cursos DELE e intensivos del Instituto Cervantes, rechazados correctamente por primera vez con el prompt actualizado. El caso de exposición en curso con fecha de cierre explícita (DD-035, provisional) se aplicó de forma consistente en 2-3 posts (Maison du Mexique, MEP).
**Siguiente paso:** decidir si vale la pena aplicar el fix de DD-037 (ventana de fecha + separador con punto) antes de la corrida de producción real sobre las 43 cuentas, o correr la producción tal cual y aceptar el ~3% de falsos negativos conocido.

---

---

## RUN-021 — Producción de extracción (43 cuentas, DD-037 aplicado) + primer --dry-run real del resolver rediseñado (DD-040/DD-041)

**Fecha:** 2026-08-13
**Extracción (producción, sin --dry-run):** 304 posts procesados sobre las 43 cuentas curadas, `--max-posts 0`. 30 eventos creados. 48.3% de ahorro de llamadas a Capa 3 confirmado en producción (coincide con RUN-020). Un crash de conexión (`SessionExpired`/`SSLEOFError`) a mitad de corrida, resuelto re-ejecutando (idempotente vía `eventExtracted`); auditoría posterior de los eventos escritos detectó y corrigió los bugs de DD-038 y DD-039 (6 eventos defectuosos identificados por ID y borrados por Diego).
**Resolver — `4_enrich_events_resolve.py --dry-run` (primera corrida sobre datos reales, algoritmo DD-040/DD-041):** 664 eventos, 220,116 pares evaluados (~20s con la matriz de similitud vectorizada).
- 68 pares duplicados encontrados → 189 relaciones redirigidas (cadenas transitivas: A→B→C consolidan a un canónico).
- 12,375 descartados por similitud insuficiente; 165,018 descartados por fecha fuera de ventana ±3d; **6 descartados por conflicto geográfico (DD-041)**.
- **DD-041 confirmado en vivo:** los 4 casos concretos que motivaron el guardrail ya no aparecen como fusión — `Portugal`/`@osullivans_bastille` (el caso original), `París`/`Madrid`, `Envigado`/`Francia`, `Ecuador`/`Paris` — todos ahora en la muestra de bloqueados en vez de en la de fusiones.
- **Límite nuevo observado, no cubierto por DD-041:** `evt_10f3fc744b97` sigue absorbiendo 3 posts sobre distintas fiestas de visualización del partido Colombia-Portugal en bares distintos de París (`@osullivans_bastille`, sin location explícita, `Nix Nox – 6 Port de la Gare 75013`) — mismo día, texto genérico similar, misma ciudad, por lo que el guardrail geográfico (que compara países) no aplica. Es un problema distinto al que resolvió DD-041: ahí el conflicto era entre países, acá es entre venues específicos dentro de la misma ciudad. No implementado — pendiente de decisión de Diego (ver runs_log).
- Título en el log de fusión (fix DD-041 secundario) funcionando correctamente en todas las líneas.
**Decisión de Diego sobre el límite residual (venues distintos, mismo partido):** no vale la pena un guardrail adicional — un evento de partido de la selección Colombia es un evento nacional masivo sin valor sociológico diferencial por venue específico, así que da igual que `evt_10f3fc744b97` absorba las 3 fiestas de visualización en bares distintos. Se aplica el resolver tal cual (68 fusiones). Tarea #11 cerrada.

---

*Última actualización: 2026-08-13*
*Próximo run: `python 4_enrich_events_resolve.py` (sin --dry-run) para aplicar las 68 fusiones. Luego: edición del dashboard (Diego especifica el alcance) y estructura de la tesis en LaTeX.*
