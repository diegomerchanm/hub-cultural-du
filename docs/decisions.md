# Hub Cultural — Registro de Decisiones de Arquitectura

> Documento vivo que registra las decisiones técnicas y metodológicas
> del proyecto, sus razones y las alternativas consideradas.
> Base para la redacción del capítulo metodológico del mémoire.

---

## DD-001 — Neo4j como base de datos principal

**Fecha:** Junio 2026
**Decisión:** Usar Neo4j AuraDB (cloud) como almacenamiento principal del proyecto.
**Razón:** Los datos de Instagram son naturalmente relacionales — cuentas que mencionan cuentas, posts que etiquetan usuarios, hashtags compartidos entre publicaciones. Un grafo captura esta estructura de forma nativa. Las consultas de red (caminos más cortos, vecindades, centralidad) son 10-100x más eficientes en Cypher que en SQL.
**Alternativa considerada:** PostgreSQL con tablas de relaciones o MongoDB documental.
**Por qué se descartó:** Las consultas de análisis de red son complejas y lentas en SQL. MongoDB no tiene soporte nativo para algoritmos de grafos.

---

## DD-002 — Apify como plataforma de scraping

**Fecha:** Junio 2026
**Decisión:** Usar Apify Cloud con actores `instagram-profile-scraper` e `instagram-post-scraper`.
**Razón:** Instagram bloquea activamente el scraping directo. Apify mantiene actores especializados que sortean estas protecciones, con infraestructura cloud que evita bloqueos por IP. El modelo de pago por uso (FinOps) permite controlar costos por query.
**Alternativa considerada:** Selenium/Playwright propio, Instaloader.
**Por qué se descartó:** Alto mantenimiento ante cambios de Instagram, riesgo de bloqueo de IP, sin infraestructura cloud.

---

## DD-003 — Separación de scripts por responsabilidad

**Fecha:** Junio 2026
**Decisión:** Scripts separados para perfiles (`1_harvest_ig_profiles.py`) y posts (`1_harvest_ig_posts.py`) en vez de un scraper único.
**Razón:** Perfiles y posts tienen estructuras JSON completamente diferentes, actores de Apify distintos, y frecuencias de actualización diferentes. Separarlos permite ejecutarlos independientemente y controlar costos granularmente.
**Alternativa considerada:** Un solo script que extrae todo.
**Por qué se descartó:** Acoplamiento excesivo, difícil de mantener y de controlar costos.

---

## DD-004 — Modelo de grafo: nodos y relaciones

**Fecha:** Junio 2026
**Decisión:** Modelo con 7 tipos de nodos (Account, Post, IgtvVideo, Hashtag, Location, Track, Comment) y 10 tipos de relaciones (PUBLISHED, HAS_HASHTAG, MENTIONS, TAGS_USER, COAUTHORED_BY, TAGGED_AT, USES_MUSIC, WROTE, ON, RELATED_TO).
**Razón:** Captura tanto el contenido (posts, hashtags) como las relaciones sociales (menciones, etiquetas) y el contexto cultural (música, ubicación). Permite análisis multidimensional de la red.
**Alternativa considerada:** Modelo simplificado solo con Account y Post.
**Por qué se descartó:** Perdería información valiosa sobre eventos (Location), tendencias culturales (Hashtag, Track) y colaboraciones (COAUTHORED_BY).

---

## DD-005 — Labels :Public/:Private en nodos Account

**Fecha:** Junio 2026
**Decisión:** Usar labels adicionales de Neo4j (`:Public`, `:Private`) en vez de solo una propiedad booleana.
**Razón:** Las labels en Neo4j permiten filtrar en Cypher de forma más eficiente (`MATCH (a:Account:Public)`) y son visualmente distinguibles en Neo4j Browser con colores diferentes.
**Alternativa considerada:** Propiedad `a.isPrivate = true/false`.
**Por qué se descartó:** Las propiedades booleanas no permiten filtrado indexado tan eficiente como las labels en Neo4j.

---

## DD-006 — Estrategia de expansión de red: BFS desde seed

**Fecha:** Junio 2026
**Decisión:** Usar `@consuladocolparis` como cuenta semilla y expandir la red via menciones, etiquetas y relatedProfiles en vez de scraping aleatorio.
**Razón:** El consulado es el nodo institucional más central de la diáspora colombiana en París — todas las cuentas relevantes están a 1-3 grados de distancia. La expansión BFS garantiza que cada nueva cuenta descubierta tiene conexión real con la comunidad.
**Alternativa considerada:** Lista curada manualmente de cuentas colombianas en París.
**Por qué se descartó:** Sesgo del investigador, no escalable, pierde conexiones inesperadas.

---

## DD-007 — Embeddings semánticos sobre keywords para detección de eventos

**Fecha:** Julio 2026
**Decisión:** Usar `paraphrase-multilingual-MiniLM-L12-v2` como Capa 1 de detección de eventos en vez de keywords hardcodeadas.
**Razón:** Los posts de Instagram mezclan español, francés e inglés con emojis y jerga. Las keywords fallan ante variaciones ortográficas, multilingüismo y formas indirectas de anunciar eventos. Los embeddings capturan el concepto de "evento futuro en lugar físico" independientemente del idioma o las palabras exactas. Como se concluyó durante el desarrollo: "clasificar por palabras en la era de la IA no tiene sentido."
**Alternativa considerada:** Regex + keywords hardcodeadas, EntityRuler de spaCy.
**Por qué se descartó:** Frágil ante variaciones, requiere mantenimiento constante, no multilingüe por diseño.

---

## DD-008 — Modelo multilingüe mDeBERTa sobre NLI monolingüe

**Fecha:** Julio 2026
**Decisión:** Usar `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` para clasificación zero-shot de tipo de evento.
**Razón:** El modelo anterior (`cross-encoder/nli-MiniLM2-L6-H768`) fue entrenado en MNLI en inglés — al pasarle captions en español y francés el resultado era cercano al azar. El mDeBERTa multilingüe fue entrenado en XNLI con 15 idiomas incluyendo español y francés.
**Alternativa considerada:** `cross-encoder/nli-MiniLM2-L6-H768` (monolingüe inglés).
**Por qué se descartó:** Tasa de detección de 0.7% — inaceptable. Bug identificado por revisión externa (Fable).

---

## DD-009 — Arquitectura de 2 capas para detección de eventos

**Fecha:** Julio 2026
**Decisión:** Pipeline de 2 capas: Capa 1 (similitud semántica con embeddings) filtra candidatos, Capa 2 (zero-shot NLI) clasifica el tipo de evento solo sobre candidatos.
**Razón:** Correr el modelo NLI sobre todos los posts es 5-7 horas en CPU i5. Usar embeddings como pre-filtro reduce los candidatos al ~40% del corpus, bajando el tiempo total a ~18 minutos. La Capa 1 es 100x más rápida y suficientemente precisa para el filtrado inicial.
**Alternativa considerada:** Solo zero-shot sobre todos los posts, SetFit fine-tuned.
**Por qué se descartó:** Zero-shot solo: demasiado lento. SetFit: requiere ~200 ejemplos etiquetados manualmente y GPU — fuera de scope para v1.

---

## DD-010 — 100 frases de referencia para Capa 1

**Fecha:** Julio 2026
**Decisión:** Usar 100 frases de referencia que cubren 8 tipos de evento en 3 idiomas y 6 formas de anuncio (convocatoria, apertura, agenda, fecha+lugar, inscripción, recordatorio).
**Razón:** Con pocas referencias (<15), el espacio semántico de comparación es demasiado estrecho — posts con formas indirectas de anuncio no son detectados. 100 frases cubren suficientemente el espacio semántico sin ser redundantes.
**Alternativa considerada:** 15 frases genéricas.
**Por qué se descartó:** Tasa de detección insuficiente — los posts más interesantes (eventos pequeños comunitarios) usan formas de anuncio no cubiertas por pocas referencias.

---

## DD-011 — nlp_event_resolver para deduplicación semántica

**Fecha:** Julio 2026
**Decisión:** Script separado que fusiona eventos duplicados usando triple criterio: misma Location normalizada + fecha ±3 días + similitud coseno > 0.75.
**Razón:** Múltiples cuentas publican sobre el mismo evento (DichaFest aparecía en 6-7 posts de diferentes cuentas). Sin deduplicación, el mismo evento se crea múltiples veces con datos fragmentados. La triple señal evita fusiones incorrectas entre eventos distintos que comparten solo un criterio.
**Alternativa considerada:** Solo similitud semántica, clustering DBSCAN.
**Por qué se descartó:** Solo similitud: fusiona eventos distintos en el mismo venue. DBSCAN: requiere definir epsilon sin datos de calibración.

---

## DD-012 — Filtro político: penalización vs exclusión total

**Fecha:** Julio 2026
**Decisión:** Las cuentas políticas (`@gustavopetrourrego`, `@registraduria`, etc.) reciben una penalización en el `culturalRelevanceScore` (weight = 0.1) en vez de ser excluidas del grafo.
**Razón:** Excluirlas eliminaría conexiones reales — el consulado interactúa con estas cuentas y esa interacción es datos válidos. La penalización las mantiene en el grafo para análisis de red pero las hace irrelevantes para las recomendaciones de semillas y el dashboard cultural.
**Alternativa considerada:** Exclusión total del grafo, exclusión solo del dashboard.
**Por qué se descartó:** Exclusión total distorsiona los algoritmos de centralidad al eliminar nodos con muchas conexiones reales.

---

## DD-013 — NetworkX/igraph local sobre Neo4j GDS

**Fecha:** Julio 2026
**Decisión:** Correr los algoritmos de análisis de red (PageRank, Betweenness, Leiden) localmente con igraph (C) en vez de usar Neo4j Graph Data Science.
**Razones:**
1. El puerto 7687 de Neo4j Aura está bloqueado en la red corporativa — GDS requiere conexión persistente.
2. igraph en C es más rápido que GDS para grafos de este tamaño (~7,000 nodos).
3. Los resultados en CSV son versionables en git y reproducibles sin conexión.
4. GDS en Aura Free tiene limitaciones de memoria no documentadas.
**Alternativa considerada:** Neo4j GDS via Bolt, Google Colab con GDS.
**Por qué se descartó:** Dependencia de red y de credenciales cloud para cada ejecución.

---

## DD-014 — Grafo completo para algoritmos, tier solo en reporte

**Fecha:** Julio 2026
**Decisión:** Los algoritmos de centralidad (PageRank, Betweenness, Leiden) corren sobre el grafo completo (4,637 nodos). El filtro por tier (primary/secondary/excluded) solo se aplica al reporte final y a la selección de semillas.
**Razón:** El PageRank de `@dichaparis` depende de quién la menciona — incluyendo cuentas `unknown`. Si se filtra el grafo antes de correr los algoritmos, se pierde ese contexto y los rankings son menos representativos de la importancia real de cada cuenta en la red.
**Alternativa considerada:** Filtrar nodos y aristas antes de construir el grafo igraph.
**Por qué se descartó:** Con ~36 nodos clasificados en 4,637 totales, el grafo filtrado es demasiado pequeño para que los algoritmos sean estadísticamente significativos.

---

## DD-015 — businessCategoryName de Instagram como fuente de actorType

**Fecha:** Julio 2026
**Decisión:** Usar el campo `businessCategoryName` de la API de Instagram (vía Apify) como clasificador primario del tipo de cuenta, complementado con `manual_overrides` en `config/account_tiers.json`.
**Razón:** Instagram ya clasifica sus cuentas de negocio con categorías precisas (Artist, Restaurant, NGO, Politician, etc.). Es más confiable que heurísticas de keywords sobre el username o la bio. Los `manual_overrides` permiten corregir clasificaciones incorrectas sin tocar el código.
**Alternativa considerada:** Zero-shot classification sobre la bio, heurísticas de keywords en username.
**Por qué se descartó:** Keywords: frágil y requiere mantenimiento. Zero-shot: añade latencia y costo computacional innecesario cuando Instagram ya tiene la clasificación.

---

## DD-016 — Sistema de tiers para priorización de cuentas

**Fecha:** Julio 2026
**Decisión:** Tres tiers de cuentas basados en `businessCategoryName`:
- **Primary:** Artist, Restaurant, Community, Local business, Podcast, Art Gallery, Journalist — foco del análisis cultural.
- **Secondary:** Language School, NGO, Education, University, Digital creator, Public figure, Entrepreneur — contexto relevante pero no prioritario.
- **Excluded:** Financial service, Politician, Real Estate, Tour Agency, Government — ruido o fuera de scope cultural (excepción: cuentas seed del Bloque A de DD-022 se mantienen activas como ancla estructural pese a este tier — ver DD-026).
**Razón:** No todas las cuentas tienen el mismo valor para el objetivo del proyecto. Las instituciones financieras y políticas distorsionan los rankings culturales. Los tiers permiten análisis granular sin perder datos.
**Actualización (Julio 2026):** Digital creator, Public figure y Entrepreneur se movieron de primary a secondary. Justificación: categorías ambiguas que no garantizan contenido cultural primario — pueden ser relevantes pero no prioritarias. Un "Digital creator" puede ser influencer de moda o de fitness sin ninguna vinculación cultural colombiana; un "Public figure" puede ser político o deportista.
**Alternativa considerada:** Incluir todo sin filtro, excluir manualmente caso por caso.
**Por qué se descartó:** Sin filtro: rankings dominados por cuentas con millones de seguidores sin relevancia cultural. Manual: no escalable.

---

## DD-017 — Almacenamiento histórico de runs de análisis

**Fecha:** Julio 2026
**Decisión:** Cada ejecución de `3_analyze_network.py analyze` guarda sus resultados en `data_processed/runs/YYYYMMDD_HHMMSS_{label}/` además de actualizar los archivos raíz.
**Razón:** El análisis de red se recalcula cada vez que se añaden datos al grafo. Guardar runs históricos permite comparar cómo evolucionan los rankings a medida que el corpus crece — evidencia del proceso iterativo para el mémoire.
**Alternativa considerada:** Sobrescribir siempre los mismos archivos.
**Por qué se descartó:** Pierde la trazabilidad de la evolución del análisis — imposible comparar V1 con V2.

---

## DD-018 — Nominatim (OpenStreetMap) para geocodificación

**Fecha:** Julio 2026
**Decisión:** Usar Nominatim (API gratuita de OpenStreetMap) para geocodificar los nodos Location extraídos por NER.
**Razón:** Gratuito, sin API key, cubre París y Colombia con buena precisión. El rate limit de 1 req/s es manejable para el volumen del proyecto (~215 locations).
**Alternativa considerada:** Google Maps Geocoding API, HERE Maps.
**Por qué se descartó:** Google Maps: de pago con límite de requests gratuitos insuficiente. HERE: requiere registro y API key.

---

## DD-019 — Nomenclatura harvest/build/analyze/enrich/visualize

**Fecha:** Julio 2026
**Decisión:** Renombrar todos los scripts con prefijo descriptivo + número de etapa (`1_harvest_`, `2_build_`, `3_analyze_`, `4_enrich_`, `5_visualize_`).
**Razón:** La nomenclatura original (`extract_profiles.py`, `nlp_extract_events.py`) no comunicaba el orden de ejecución ni el rol de cada script. El nuevo esquema es autodescriptivo — cualquier colaborador entiende el pipeline sin leer documentación.
**Alternativa considerada:** Mantener nombres originales, usar solo números.
**Por qué se descartó:** Solo números: no descriptivos. Nombres originales: no comunican orden ni responsabilidad.

---

## DD-020 — Baja densidad del grafo como limitación metodológica

**Fecha:** Julio 2026
**Decisión:** Documentar explícitamente la baja densidad del grafo (2,047 nodos / 2,300 aristas, ratio ~1.1 aristas/nodo) como limitación metodológica de v1 en vez de intentar ocultarla.
**Razón:** La limitación es real y afecta el poder discriminativo de los algoritmos de centralidad. Documentarla con honestidad y proponer el BFS de V2 como solución es más riguroso académicamente que presentar resultados sin contexto.
**Implicación para V2:** Scrapeando 50 posts para cada una de las 170 cuentas actuales (en vez de 12) se triplicaría la densidad del grafo sin necesidad de expandir el número de cuentas.
**Referencia:** La baja densidad puede interpretarse también como hallazgo sociológico — una red muy dispersa puede indicar fragmentación de la diáspora colombiana en Francia, consistente con literatura sobre diásporas en países de acogida con alta individualización (Vertovec, 2009).

---

---

## DD-022 (actualización) — Seeds V2: consulados + instituciones culturales

**Fecha:** 2026-07-07

Contexto: los consulados conocen sus comunidades — sus relatedProfiles y menciones son un mapa de la diáspora real. Se amplía el criterio de seeds a instituciones culturales francesas relacionadas con América Latina.

Decisión: la clasificación de importancia/tier de cada cuenta NO se define aquí manualmente. Se determinará después mediante scraping de seguidores/relatedProfiles (`1_harvest_ig_profiles.py`) y el scoring del clasificador NLP (`1_harvest_account_classifier.py` — geography_score + cultural_score + anti-embeddings, DD-023). Este bloque solo fija el set inicial de seeds, no su tier final.

### Bloque A — Consulados y embajadas latinoamericanas en Francia

| País | Handle IG | Tipo | Confianza |
|---|---|---|---|
| Colombia | @consuladocolparis | Consulado | Alta |
| Colombia | @embajadacolfra | Embajada | Alta |
| Argentina | @arg_enfrancia | Embajada | Media-alta |
| Brasil | @cg_brasil_paris | Consulado | Media (verificar vs @cgparisoficial) |
| Brasil | @bresilenfrance | Embajada | Alta |
| Chile | @embachilefrancia | Embajada | Media |
| México | @embajadademexicoenfrancia | Embajada | Media-alta |
| Perú | @consuladodelperuenparis | Consulado | Alta |
| Venezuela | @embfrancia_ve | Embajada | Baja (verificar vs @embavefrancia) |
| Ecuador | @eecufrancia | Embajada | Baja (verificar vs @embajadaecufrancia) |
| Uruguay | @uruguayfrancia | Embajada | Alta |
| Bolivia | — | No encontrado en IG | — |
| Costa Rica | @costaricafrance | Embajada | Media-alta |
| Guatemala | @embaguafr | Embajada | Alta |
| República Dominicana | @rdenfrancia | Embajada | Alta |
| República Dominicana | @rdenparis | Consulado | Alta |
| Panamá | @embpanamafra | Embajada | Alta |
| Cuba | @embacubafrancia | Embajada | Alta |
| El Salvador | — | No encontrado en IG | — |
| Honduras | @embajadadehondurasenfrancia | Embajada | Media-alta |
| Nicaragua | — | No encontrado en IG | — |
| Paraguay | — | No encontrado en IG | — |

### Bloque B — Instituciones culturales/académicas francesas relacionadas con América Latina

| Cuenta | Handle IG | Tipo |
|---|---|---|
| Maison de l'Amérique Latine | @maisondelameriquelatineparis | Institución cultural (1946) |
| Instituto Cervantes París | @institutocervantesparis | Instituto cultural hispano |
| France Diplomatie (ES) | @francediplo_es | Institucional francés (español) |
| IHEAL & CREDA | @iheal_creda | Centro académico (Sorbonne Nouvelle) |
| Festival CLaP | @festivalclap | Festival de cine latinoamericano de París |
| GRULAC UNESCO | @grulacunesco | Grupo diplomático LatAm/Caribe en UNESCO |
| El Café Latino | @elcafelatino | Medio bilingüe sobre América Latina en Europa |

Excluido explícitamente: Alliances Françaises (fuera de Francia), cuentas comerciales (ej. restaurante de la Maison de l'Amérique Latine).

Pendiente de verificación manual por Diego:
- Bolivia, El Salvador, Nicaragua, Paraguay — sin handle de IG confirmado.
- Brasil, Venezuela, Ecuador — doble candidato, elegir cuenta activa antes de correr el harvester.

---

---

## DD-025 — Cuentas vacías como lista de prospección orgánica

**Fecha:** Julio 2026
**Decisión:** Tratar las ~4,467 cuentas vacías en Neo4j como lista de prospección prioritaria para V2, antes de buscar nuevas seeds externas.
**Razón:** Estas cuentas fueron descubiertas orgánicamente porque la diáspora colombiana ya las mencionó, etiquetó, comentó o relacionó. Su origen confirma relevancia:
- 1,530 via comentarios en posts del consulado
- 1,001 via relatedProfiles de 36 perfiles scrapeados
- 906 via menciones en posts
- 740 via etiquetas en posts
- 290 origen desconocido (múltiples fuentes)

Los relatedProfiles son especialmente valiosos — 36 perfiles generan potencialmente ~1,300 cuentas únicas nuevas sin necesidad de scraping de followers. Costo estimado para scrapeear todos sus perfiles: ~$0.65 USD.

**Implicación para V2:** Correr `1_harvest_ig_profiles.py` sobre las cuentas vacías filtradas por el clasificador NLP antes de buscar seeds externas — son candidatas con relevancia orgánicamente confirmada.
**Alternativa considerada:** Buscar nuevas seeds externas (consulados latinoamericanos) como primera acción de V2.
**Por qué complementar ambas:** Las seeds externas amplían el scope a toda América Latina; las cuentas vacías profundizan el corpus colombiano ya existente.

---

## DD-026 — Consulados como ancla estructural vs. objetivo cultural clasificable

**Fecha:** 2026-07-09
**Decisión:** Las cuentas del Bloque A de seeds V2 (consulados y embajadas 
latinoamericanas en Francia, DD-022) reciben `role = "seed_source"` y 
`keep = False` de forma incondicional en `1_harvest_account_classifier.py`, 
independientemente de su `final_score`. Esto se implementa en la función 
`_finalize()` (líneas ~531-540): si la cuenta es seed y pertenece al 
Bloque A, `keep` se sobreescribe a `False` sin importar el score calculado.

**Razón — la contradicción que resuelve:** DD-016 clasifica "Government 
organization" en el tier Excluded, ya que instituciones gubernamentales 
distorsionan los rankings de relevancia cultural. Pero DD-006 y DD-022 
usan precisamente instituciones gubernamentales (consulados/embajadas) 
como semilla estructural de todo el descubrimiento de red — sin ellas no 
hay BFS, no hay `relatedProfiles`, no hay expansión. Aplicar DD-016 
literalmente excluiría del grafo a la cuenta que hace posible el grafo.

La resolución separa dos preguntas distintas que antes se resolvían con 
un solo criterio:
1. "¿Esta cuenta es un objetivo cultural válido para el análisis de 
   relevancia?" → Para consulados/embajadas: NO (se comportan igual que 
   DD-016 lo predice — son gobierno, no cultura).
2. "¿Esta cuenta debe permanecer activa como nodo/ancla para descubrir 
   la red?" → SÍ, siempre — su función no es ser evaluada, es generar 
   las conexiones que sí serán evaluadas.

El score alto que estas cuentas obtienen (ej. @consuladocolparis: 
geo=1.00, cult=0.88, final=0.94) es correcto y no se descarta — 
confirma que el modelo detecta bien geografía+cultura — pero no se 
traduce en `keep=True` porque `keep` responde a la pregunta 1, no a la 2.

**Distinción con DD-012:** DD-012 penaliza (no excluye) cuentas políticas 
individuales para preservar aristas reales en el grafo. DD-026 es un 
mecanismo distinto: no es una penalización de score, es una separación 
de rol (seed_source vs. target) que aplica solo al Bloque A de seeds 
institucionales, no a cuentas políticas descubiertas orgánicamente.

**Alternativa considerada:** Aplicar DD-016 sin excepción (excluir 
también a los consulados del grafo activo); crear un tier adicional 
"institutional-seed" con reglas propias de scoring.
**Por qué se descartó:** Excluir consulados del grafo activo rompe la 
cadena de descubrimiento BFS (DD-006) — no habría forma de encontrar 
las cuentas de la diáspora sin la cuenta que las conecta. Un tier nuevo 
añadiría complejidad de scoring innecesaria cuando el problema real es 
de rol (fuente vs. objetivo), no de score.

---

*Última actualización: 2026-07-09*
*Próximas decisiones a documentar: DD-023 (clasificador NLP de cuentas), SetFit para v2, integración TikTok, human-in-the-loop para revisión de eventos.*
