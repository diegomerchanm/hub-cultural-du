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

## DD-027 — Métrica de completitud de datos como diagnóstico (no afecta scoring aún)

**Fecha:** 2026-07-09
**Contexto:** Tras el primer scrapeo de las 25 seeds V2, el grafo pasó de 
4,637 a 5,433 nodos :Account (+796). De esos, 2,665 cuentas nuevas tienen 
`fullName` (llegaron vía relatedProfiles/taggedUsers/coauthorProducers) y 
2,575 no tienen ningún campo más allá del username (llegaron vía mentions 
o comentarios). `1_harvest_account_classifier.py` no distinguía esto — 
trataba a todas las cuentas sin perfil scrapeado con el mismo factor de 
confianza fijo (USERNAME_CONF=0.60), sin importar cuánta evidencia real 
había disponible. Además, el export a nodes.csv perdía las propiedades 
`verified`, `private` y `profilePicUrl` que sí llegan a Neo4j desde 
2_build_graph.py.

**Decisión:** Agregar `data_completeness` (0-1, conteo de campos no-nulos 
sobre 5: fullName, followers, public, verified, profilePicUrl) como columna 
de diagnóstico en account_scores.csv y en la salida de --diagnose. Por 
ahora NO modula el cálculo de final_score ni el umbral de keep — es 
únicamente para que el análisis visual del umbral (a ojo, según lo acordado) 
tenga en cuenta la calidad de la evidencia detrás de cada score, no solo 
el score mismo.
**Razón:** Comparar un final_score=0.45 de una cuenta con bio+posts reales 
contra un final_score=0.45 de una cuenta solo-username no es comparar lo 
mismo — la validez de la medición es distinta. Documentar la métrica antes 
de decidir cómo usarla evita comprometerse prematuramente a una fórmula 
de ponderación sin haber visto la distribución real.
**Alternativa considerada:** Modular USERNAME_CONF automáticamente según 
completitud desde ya.
**Por qué se pospuso:** Diego quiere revisar la distribución real de 
data_completeness cruzada con final_score antes de decidir si y cómo debe 
pesar — evita ajustar una fórmula con datos que aún no se han visto.

---

## DD-028 — Posts recientes sobre densidad histórica

**Fecha:** 2026-07-12
**Decisión:** 1_harvest_ig_posts.py filtra por `onlyPostsNewerThan` 
(default 10 días) en vez de solo un tope de cantidad (RESULTS_LIMIT=50 
sin filtro temporal, como en V1/RUN-003). Lista de cuentas generalizada 
desde account_scores.csv (keep=True), con exclusión manual de 
williamsanchezinmobiliaria (falso positivo del clasificador — categoría 
de negocio no capturada por el tier).
**Razón:** DD-020 (V1) buscaba maximizar densidad del grafo scrapeando 
más posts por cuenta, bajo la lógica de que más aristas sociales = 
mejor discriminación de algoritmos de centralidad (GDS/igraph). En V2, 
la idoneidad cultural de una cuenta ya no depende de esos algoritmos — 
se resuelve directamente con el clasificador NLP sobre bio+posts+
username (DD-023, DD-027). Esto libera a la fase de posts de la 
responsabilidad de generar densidad, y permite priorizar el objetivo 
real del pipeline de eventos (4_enrich_events_extract.py): capturar 
anuncios de eventos vigentes, no reconstruir historial.
**Alternativa considerada:** Mantener RESULTS_LIMIT=50 sin filtro 
temporal (como V1).
**Por qué se descartó:** Trae posts de hace meses/años que no aportan 
a la detección de eventos próximos, y diluye el corpus con contenido 
desactualizado que ya no representa la actividad cultural vigente de 
la diáspora.
**Riesgo aceptado:** Cuentas institucionales de baja cadencia de 
publicación pueden quedar con 0 posts en la ventana de 10 días — se 
diagnostica en la corrida (punto 3) y se revisa caso por caso si el 
volumen de "vacíos" es alto.

---

## DD-029 — Ventana de scrapeo dinámica por cuenta + cap deslizante de 50 posts

**Fecha:** 2026-07-13
**Decisión:** 1_harvest_ig_posts.py reemplaza el chequeo binario vigente/
vencido (DD-028) por una ventana `onlyPostsNewerThan` calculada 
dinámicamente por cuenta: min(días desde el último post conocido, 
--days tope). Cuentas con brecha <1 día se saltan; el resto se 
re-chequea con exactamente la ventana que necesita, no un valor fijo 
global. Los resultados se fusionan con los posts existentes (dedupe 
por id) y se recortan a los 50 más recientes (ventana deslizante), en 
vez de sobreescribir el archivo completo.
**Razón:** El chequeo binario de DD-028 tenía un punto ciego: una 
cuenta con post de hace 2 días se marcaba "vigente" bajo cualquier 
ventana ≥2 días y se saltaba por completo, perdiendo posts publicados 
en el intervalo entre ese post conocido y "ahora". La ventana dinámica 
cierra exactamente esa brecha por cuenta, sin gastar de más en cuentas 
ya casi al día ni quedarse corto en cuentas con actividad reciente 
justo fuera del radar del chequeo binario.
**Alternativa considerada:** Mantener ventana fija global (DD-028 tal 
cual) y aceptar el punto ciego.
**Por qué se descartó:** El costo marginal de consultar con ventana 
pequeña es casi nulo (confirmado empíricamente: $0.00-0.02 por cuenta 
en corridas previas), así que no hay razón de peso para tolerar el 
punto ciego solo por ahorro de llamadas.
**Nota técnica:** El cap de 50 posts (RESULTS_LIMIT) ahora funciona 
como ventana deslizante acumulativa, no como límite de una sola 
corrida — el corpus por cuenta converge a "los 50 posts más recientes 
conocidos hasta la fecha", actualizado incrementalmente en cada corrida.

---

## DD-030 — Detección explícita del placeholder de error "no_items" de Apify

**Fecha:** 2026-07-15
**Decisión:** Agregar `_is_error_placeholder(items)` en 1_harvest_ig_posts.py
para detectar el caso en que `apify/instagram-post-scraper` devuelve una lista
de 1 elemento con clave `"error"` y sin `"id"`, antes de pasar al branch de
merge. El chequeo en `scrape_posts()` pasa de `if not dataset_items` a
`if not dataset_items or _is_error_placeholder(dataset_items)`.
**Razón:** Cuando el actor no encuentra contenido (cuenta privada, sin posts
en la ventana, o perfil restringido), devuelve:
`[{"url": ..., "inputUrl": ..., "requestErrorMessages": [], "error": "no_items",
"errorDescription": "Empty or private data for provided input"}]`.
Esta lista de 1 elemento es **truthy** en Python — `if not dataset_items`
nunca entraba al branch de diagnóstico. El resultado era: (a) los contadores
`window_empty`/`no_content`/`unknown` no se incrementaban, (b) el log
registraba 1 "post" falso, y (c) en versiones anteriores a DD-029 (sin
merge_and_cap), el archivo existente se sobreescribía con datos vacíos o con
el placeholder mismo. Confirmado como causa de pérdida de datos en RUN-013
para elcafetal.paris, educulturaco e ivan_argote.
**Criterio de detección:** `len(items) == 1 and "error" in items[0] and "id"
not in items[0]` — conservador: no filtra posts reales con un solo ítem, ya
que los posts reales siempre tienen `"id"`.
**Alternativa considerada:** Chequear solo `items[0].get("error") ==
"no_items"` (string hardcodeado).
**Por qué se descartó:** La condición compuesta (len==1 + "error" en item +
no "id") es más robusta ante cambios en el string exacto del campo error, y
más difícil de disparar accidentalmente contra un post real.

---

## DD-031 — Bounding box geográfico para penalizar cuentas fuera de Francia

**Fecha:** 2026-07-15
**Decisión:** En `geo_hard_signals()`, antes de aplanar `businessAddress` a
string, conservar el dict original para leer `latitude`/`longitude`. Si las
coordenadas caen fuera del bbox de Francia metropolitana
(`lat: 41.0–51.5, lon: -5.5–9.7`), aplicar `penalty = 0.90` al
`geography_score` (`geography = max(0.0, geography - 0.90)`). El fallback
anterior (buscar ciudades LatAm en bio) se reduce a `penalty = 0.35` y solo
se activa cuando no hay lat/lon en businessAddress Y no hay ninguna señal
positiva de Francia (signals list vacía) — para no penalizar patrones
legítimos de la diáspora como "de Bogotá a París".
**Hallazgo que motivó el cambio:** La verificación manual de JSONs crudos
confirmó que `businessAddress` tiene `latitude`/`longitude` reales en casi
todos los casos poblados, pero NO tiene `countryCode`. El bug original (DD-030
predecessor) se pensó que afectaba solo a las Alianzas Francesas colombianas,
pero al verificar `businessAddress` en perfiles reales se encontraron cuatro
cuentas adicionales afectadas: `williamsanchezinmobiliaria` (España),
`unadunioneuropea` (Madrid), `embcolghana` (Ghana), `remaxmariavillasmil02`
(Venezuela) — todas con lat/lon claramente fuera de Francia pero sin texto de
ciudades LatAm en su bio, lo que hacía invisible el bug para el fallback
anterior.
**Por qué bbox en lugar de lista de ciudades:**
- Generaliza a cualquier país sin mantenimiento de listas.
- Detecta coordenadas de Madrid, Accra, Caracas, Bogotá, etc. con la misma
  regla, sin necesidad de añadir cada caso nuevo.
- Las listas de ciudades tienen falsos positivos ("Cartagena" en España,
  "Valencia" en Venezuela), el bbox no.
**Limitación aceptada:** El bbox excluye los DOM-TOM franceses (Guadalupe,
Martinica, Reunión, etc., lat < 41.0 o lon fuera del rango). Decisión
deliberada: el proyecto se enfoca en la diáspora latinoamericana en Francia
metropolitana/Île-de-France. Si en el futuro se quiere cubrir DOM-TOM,
revisar `FRANCE_BBOX` o añadir sub-bboxes por región.
**Alternativa considerada:** Lista de `countryCode` válidos para Francia.
**Por qué se descartó:** `businessAddress` de la API de Instagram no incluye
`countryCode` — campo ausente en los datos reales verificados.

---

*Última actualización: 2026-07-15*
*Próximas decisiones a documentar: DD-023 (clasificador NLP de cuentas), SetFit para v2, integración TikTok, human-in-the-loop para revisión de eventos.*
