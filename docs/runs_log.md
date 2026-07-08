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

*Última actualización: Julio 2026*
*Próximo run: RUN-011 — V2 seeds consulados latinoamericanos*
