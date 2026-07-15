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

*Última actualización: 2026-07-15*
*Próximo run: RUN-014 — 3_analyze_network.py sobre grafo V2 expandido*
