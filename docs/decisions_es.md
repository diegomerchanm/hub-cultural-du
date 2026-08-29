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

## DD-032 — Regla acotada para sedes de Alianza Francesa fuera de Francia

**Fecha:** 2026-07-15
**Decisión:** Añadir en `geo_hard_signals()` un chequeo independiente del
fallback de bio-city (DD-031): si el username del perfil coincide con
`AF_SATELLITE_PATTERN` (`alian[cz]a.{0,4}frances|alliance.{0,4}fran[cç]aise`)
Y contiene un token de `NON_FRANCE_CITY_MARKERS`, aplicar `penalty = max(penalty, 0.90)`.
Este chequeo no está gateado por "sin señales positivas de Francia" — a
diferencia del fallback de bio — porque el patrón username+ciudad LatAm es
estructuralmente inequívoco: una sede de Alianza Francesa con nombre de ciudad
LatAm en el handle es por definición una sede fuera de Francia.
**Residual que motivó el cambio:** `alianzafrancesademedellin` (Medellín,
Colombia) pasó el fix de bbox (DD-031) porque su `businessAddress` no tiene
`latitude`/`longitude`. Su bio menciona "Francia" como tema ("¡Aprende
francés! ..."), no como ubicación → dispara `sem_geo:1.00` y `bio:FR`, lo que
bloquea el fallback de bio-city por diseño. Era el único `keep=True` incorrecto
tras DD-031.
**Por qué regla acotada al patrón AF, no regla general de username:**
Una regla general "username con ciudad LatAm → penalizar" rompería cuentas de
diáspora legítimas que usan su ciudad de origen en el handle pero sí residen en
Francia: `medellin_en_paris`, `paisas_en_paris`, `bogotanos_en_paris`, etc. El
patrón AF es semánticamente distinto: "alianza francesa" + ciudad colombiana
identifica inequívocamente una institución radicada en esa ciudad colombiana, no
en Francia.
**Alternativa considerada 1:** Regla general de username con cualquier ciudad
LatAm en `NON_FRANCE_CITY_MARKERS`.
**Por qué se descartó:** Alto riesgo de falsos positivos en cuentas de diáspora
con ciudad de origen en el username.
**Alternativa considerada 2:** Override manual en `config/account_tiers.json`
(`"alianzafrancesademedellin": "excluded"`).
**Por qué se descartó:** No generaliza a futuras sedes de Alianza Francesa que
se descubran después (el BFS puede encontrar más); menos defendible como
metodología sistemática en el mémoire que una regla declarativa.

---

## DD-033 — Failover automático Groq↔Cerebras con tope de espera por rate-limit

**Fecha:** 2026-08-11
**Nota:** el código de `4_enrich_events_extract.py` referencia este número
("ver DD-033 update 2/3/7") desde antes de esta entrada — el diseño del
failover ya existía en el pipeline, pero nunca había quedado documentado
acá. Esta entrada describe el sistema tal como está hoy, más el cambio
puntual de hoy (tope de espera).
**Decisión:** `llm_enrich_event()` intenta primero el proveedor de
`LLM_PROVIDER` y, si agota sus reintentos internos sin devolver
`is_public_invitation`/`is_upcoming` utilizables, marca ese proveedor como
agotado (`_provider_failed_this_run`) y no vuelve a intentarlo por el resto
de la corrida — usa el otro proveedor cloud (Groq↔Cerebras) directamente.
Cambio de hoy: nueva constante `MAX_RATE_LIMIT_WAIT_S = 300`. Si un 429
pide esperar (`Retry-After`) más de 5 minutos, ya no se espera ni se
reintenta dentro de ese proveedor — se corta el intento ahí mismo y se
cambia de proveedor de inmediato.
**Residual que motivó el cambio de hoy:** en corridas de validación
(`eval_100_v2.csv`, `eval_101_200.csv`) se observaron esperas de ~513s en
un 429 de Groq. Como `_provider_failed_this_run` es una variable de
proceso (no persiste entre invocaciones), cada corrida nueva del script
ese mismo día repetía el ciclo completo de 3 intentos esperando el
`Retry-After` entero antes de recién ahí cambiar a Cerebras — hasta ~25
min perdidos por corrida si el tope diario de Groq (no el de por-minuto)
ya estaba agotado.
**Validación real del failover (no solo del caso 429):** en la corrida de
`eval_251_300.csv` (2026-08-11), Groq falló por errores de conectividad
real (`ReadTimeout`, `ConnectionError` — DNS/red, no 429) en vez de
rate-limit. El mecanismo existente ya cubría ese caso correctamente:
3 intentos cortos (~1.5s entre sí, sin el tope de 5 min porque no era un
429 con `Retry-After` largo) y cambio a Cerebras en menos de 90s. Sirve
como confirmación de que el diseño ya era robusto a caídas de red, no
solo a cupos agotados.
**Alternativa considerada:** cachear el estado de "proveedor agotado" en
disco entre invocaciones (ej. `.llm_provider_state.json`) para que
corridas posteriores el mismo día no vuelvan a intentar el proveedor caído
desde cero.
**Por qué se descartó (por ahora):** añade un archivo de estado más que
mantener y sincronizar; el tope de espera ya resuelve el costo más alto
(esperas largas) sin ese overhead. Revisar si correr el script muchas
veces por día se vuelve un patrón habitual.

**Update 8 (2026-08-21):** Diego pidió agregar Google (Gemini) y DeepSeek
como proveedores cloud adicionales, con orden de fallback explícito
`groq → google → deepseek → cerebras`. Implementado en `_CLOUD_PROVIDERS`
(dict ordenado — Python preserva orden de inserción, así que el orden del
dict ES el orden de fallback cuando `LLM_PROVIDER="groq"`, el default).

- **Google/Gemini** (`LLM_PROVIDER=google`, `GOOGLE_API_KEY`): usa la capa
  de compatibilidad OpenAI de Gemini (`generativelanguage.googleapis.com/
  v1beta/openai/chat/completions`) en vez de la API nativa
  (`generateContent`), para reutilizar el mismo shape de request/response
  que Groq/Cerebras sin duplicar lógica. Modelo `gemini-2.5-flash-lite`
  (mayor cupo diario gratis de los tres modelos free-tier de Gemini).
  Throttling RPM/TPM implementado igual que Groq (`_google_request`), pero
  con números conservadores/aproximados — Google no publica una tabla
  estática de límites por tier en `ai.google.dev/gemini-api/docs/rate-limits`
  (confirmado el 2026-08-21), remite a un dashboard interactivo
  (`aistudio.google.com/rate-limit`) que no se pudo consultar desde acá.
  Revisar y ajustar `GOOGLE_MAX_RPM`/`GOOGLE_MAX_TPM` una vez que Diego
  vea los límites reales en su propia cuenta de AI Studio.
- **DeepSeek** (`LLM_PROVIDER=deepseek`, `DEEPSEEK_API_KEY`): diferencia
  importante frente a los otros tres — **no tiene tier gratis**, es pago
  por token (confirmado en `api-docs.deepseek.com/quick_start/pricing`
  el 2026-08-21: `deepseek-v4-flash`, el más barato del mercado por buen
  margen, ~$0.22-0.44/M tokens de entrada y $0.66-1.32/M de salida según
  horario peak/off-peak UTC). Tampoco publica RPM/TPM — su límite es de
  concurrencia (2500 conexiones simultáneas para v4-flash), irrelevante
  para este pipeline que llama secuencialmente, así que `_deepseek_request`
  no tiene throttling propio, solo reintento en 429/error. En la práctica
  nunca "se agota" como Groq/Google/Cerebras (que sí tienen cupo diario
  gratis) — solo fallaría por saldo insuficiente o un tope de gasto que
  Diego configure en su cuenta.
- **Pendiente:** Diego mencionó querer probar la calidad del LLM de
  DeepSeek específicamente (dice ser el más barato del mercado) antes de
  confiar en él para escritura real — no se corrió ninguna comparación de
  calidad todavía. Recomendado: correr `--dry-run` con `LLM_PROVIDER=
  deepseek` sobre la misma muestra ya evaluada con Groq (ver
  `verify_events_extraction.py` / `eval_100_report.md`) antes de dejarlo
  en el orden de fallback para corridas de producción.

**Categorización manual de las 216 cuentas nuevas "fijo" (2026-08-21, sesión posterior).** Diego pidió avanzar directo con subagentes Haiku (para no gastar de más) a llenar el Excel `cuentas_instagram_completo_v4.xlsx` para las 216 cuentas nuevas del bucket `fijo` de `pilot_classification.csv` (las que no venían ya scrapeadas), con los mismos 12 campos que ya tienen las 126 cuentas originales (artType, institutionType, eventFrequency, parentInstitution, contentType, seguidores, hasFreeEvents, priceRange, promotedOutsideInstagram, eventFormat, culturalIdentity, geoZone).

- **Método:** 22 subagentes Haiku en paralelo (10 cuentas cada uno, salvo el último con 6), con 3 ejemplos reales del Excel como few-shot para calibrar estilo/formato, y formato de respuesta `username|campo1|...|campo12` separado por `|`.
- **Resultado:** 161 de 216 cuentas categorizadas con datos reales de búsqueda web (o `No confirmado` explícito por campo cuando no se encontró nada confiable). **55 cuentas sin ningún dato** — 5 lotes completos (comedie.francaise.officiel...lesarchescitoyennes; idyllesculturelles...transfugemagazine; colegio_espana_paris...athenee.theatre; librairie.libralire...maisonbasque.paris; la_fab_officiel...maisondelasuede) fallaron por agotamiento del presupuesto de `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` — mismo patrón ya documentado en la tanda 3/4 de clasificación — más 5 cuentas sueltas del último lote (maisondelitalie, maisoniledefrance, parisfacecachee, quartierartdrouot, theatre.la.fleche). Quedan marcadas como pendientes (sin fila en el Excel) para una próxima sesión.
- **Volcado al Excel:** las 161 filas se escribieron contiguas en las filas 253-413 (una fila por cuenta, sin la fila de descripción intercalada que tenía el bloque original de 126 — esa fila nunca la leyó el script de todos modos, era solo cosmética). `handle_rows()` en `load_manual_account_categorization.py` extendido para incluir ese rango.
- **Duplicado detectado y corregido:** `semaine_de_la_critique` y `citedelabd` resultaron estar tanto en el bloque original (curadas a mano antes) como en las 216 nuevas (redescubiertas por el pool de 7897) — el subagente Haiku les devolvió datos más pobres (`No confirmado` en varios campos) que la entrada original ya existente. Se vaciaron las filas nuevas (282 y 332) para no pisar la entrada de más calidad, en vez de dejar que `load_rows()` mandara ambas a Neo4j y ganara la última procesada por azar de orden.
- **Validado sin Neo4j** (no hay credenciales en este sandbox): `py_compile` limpio, y `load_rows()` corrido de forma aislada contra el `.xlsx` real — 285 filas totales (126 + 161 - 2 duplicados vaciados), 0 duplicados, 0 `geoZone` faltante.
- **Pendiente:** correr `load_manual_account_categorization.py --dry-run` de verdad (con credenciales reales) antes de escribir a Neo4j, y retomar las 55 cuentas sin categorizar en una sesión con presupuesto de búsqueda fresco.

**Hallazgo al preparar el scrape (2026-08-23): 41 de las 126 cuentas originales curadas a mano nunca se scrapearon.** Diego preguntó si además de las 216/213 nuevas había que scrapear cuentas "más antiguas" — se verificó cruzando los usernames del bloque original del Excel (filas 1-251) contra `data_raw/*.json`: **85 de las 126 ya tienen perfil scrapeado, mas 41 nunca pasaron por Apify** (aunque sí tienen categorización manual completa en el Excel desde antes). `config/seeds_idf.json` (83 seeds) resultó ser el archivo usado para un scrape anterior — coincide exactamente con un subconjunto de las 126, así que las 41 sin scrapear son las que quedaron fuera de ese archivo original. Se armó `config/seeds_originales_sin_scrapear.json` (41 seeds) para cerrar ese hueco. Sin datos reales de Instagram (bio, posts) estas 41 cuentas no pueden alimentar `4_enrich_events_extract.py` aunque ya tengan categorización manual — el scrape es un prerequisito real, no solo un nice-to-have.

- **Duplicado adicional detectado:** 3 cuentas (`citedelabd`, `holaqtaloff`, `semaine_de_la_critique`) estaban tanto en las 126 originales como en las 216 nuevas de `seeds_fijo_nuevas_cuentas.json` (redescubiertas por el pool de 7897 sin saber que ya eran conocidas). Se quitaron de `seeds_fijo_nuevas_cuentas.json` (216 → 213) para no duplicar el scrape — ya están cubiertas por `seeds_originales_sin_scrapear.json`. Dos de las tres (`citedelabd`, `semaine_de_la_critique`) ya se habían detectado como duplicado en el Excel de categorización (ver entrada anterior); `holaqtaloff` es un caso nuevo — no generó fila duplicada en el Excel porque su lote de categorización (batch 3) falló por completo, así que no había nada que pisar ahí, pero sí importaba para el scrape.
- **Plan de scrape resultante:** `config/seeds_fijo_nuevas_cuentas.json` (213) + `config/seeds_originales_sin_scrapear.json` (41) = 254 cuentas nuevas a scrapear, sin solapamiento entre sí ni con `data_raw/` existente. Pendiente que Diego corra `1_harvest_ig_profiles.py`/`1_harvest_ig_posts.py` con ambos archivos en su venv.

---

## DD-034 — Evitar falsa precisión de fecha en `extract_dates()`

**Fecha:** 2026-08-11
**Decisión:** en `extract_dates()`, el resultado del fallback
`dateparser.search_dates()` (búsqueda de fecha sobre el texto completo,
no solo sobre los fragmentos de `_DATE_RE`) solo se acepta si el
fragmento de texto que matcheó trae un dígito de día o un nombre de día
de la semana explícito (nueva función `_has_day_signal()`). Si el texto
solo menciona un mes o año suelto (p. ej. "este marzo"), se descarta el
resultado — el evento queda sin fecha en vez de con una fecha inventada.
**Razón:** `dateparser` corre con `PREFER_DAY_OF_MONTH: "first"`, que
rellena un día 1 arbitrario cuando el texto no especifica día. La ruta
regex-primero (`_DATE_RE`) ya exige dígito o nombre de día en sus 4
patrones, así que nunca fabricaba nada — el problema estaba solo en el
fallback de texto completo, que no tenía esa restricción.
**Residual que motivó el cambio:** la comparación de la clasificación
independiente de Claude contra el pipeline sobre el batch 2 (posts
101-200, `eval_101_200_comparacion.csv`) encontró 4 de 13 desacuerdos
donde el script le asignó un día calendario específico a un post que solo
mencionaba el mes (p. ej. `tamalesenparis`: "este marzo" → `2026-03-01`).
**Alternativa considerada:** guardar un campo de precisión de fecha
(`date_precision`: día/mes/año) en vez de descartar la fecha imprecisa.
**Por qué se descartó (por ahora):** requiere tocar el esquema de
`:Event`, el dedup por ventana de fechas (`dates_close`), el clamping de
1095 días y el dashboard — más trabajo del que se justifica ahora mismo.
Se prioriza no contaminar el dato con falsa certeza sobre preservar una
precisión aproximada. Revisar si en el futuro vale la pena recuperar esa
señal ("se sabe el mes, no el día").

---

## DD-035 — Exposición en curso con fecha de cierre explícita cuenta como SI en el criterio de clasificación manual

**Fecha:** 2026-08-12
**Contexto:** el criterio de clasificación independiente de Claude (usado
para medir la pipeline, no código de producción) exige como mínimo una
señal de fecha relativa o explícita en el caption para marcar un post
como evento real (ver criterio establecido en batches 1-2, sin número de
decisión propio hasta ahora). Durante el batch 3 (posts 351-400)
aparecieron 3 casos de exposiciones anunciadas con fórmula "jusqu'au
[fecha]" (hasta el [fecha]) — el texto es mayormente un recap en pasado
de la inauguración, pero incluye una fecha de cierre explícita y una
invitación implícita a visitar mientras la exposición sigue abierta.
**Decisión:** tratar "en curso hasta fecha X" como señal de fecha válida
→ SI, aunque el resto del post esté narrado en pasado. Aplica solo a
exposiciones/muestras con fecha de cierre explícita, no a posts que solo
mencionan que algo "sigue disponible" sin fecha.
**Razón:** la fecha de cierre es información accionable para un lector
que quiera asistir — a diferencia de un recap puro donde el evento ya
terminó sin ninguna ventana de acción futura. Es coherente con el
espíritu del criterio (¿puede alguien que lea este post todavía ir?) más
que con su letra literal (que fue pensada para eventos de un solo día).
**Tensión sin resolver:** el pipeline (`4_enrich_events_extract.py`)
rechazó los 3 casos — no está claro si el gate de Capa 3 debería
aprender a distinguir "exposición en curso" de "recap puro", o si esta es
una categoría que Claude está sobre-incluyendo respecto al criterio
original. Ver RUN-018 para el detalle de los 3 casos. Pendiente de
revisión cuando se llegue a los 500 posts y se analice el patrón agregado
de desacuerdos antes de decidir si ajustar el prompt/gate de Capa 3 o
revertir el criterio de Claude a "solo fecha puntual, no rango en curso".
**Alternativa considerada:** excluir estos casos del criterio SI (quedan
como NO por defecto, igual que el script).
**Por qué no se descartó de una vez:** cambiar el criterio retroactivo
sobre datos ya etiquetados (296 posts previos) sin haber visto si el
patrón es frecuente o marginal en el resto de la muestra sería prematuro
— se documenta la tensión y se decide con los 500 posts completos.

---

## DD-036 — Retuning de la pipeline a partir del análisis agregado de 495 posts (Capa 1 + gate de fecha + prompt)

**Fecha:** 2026-08-12
**Contexto:** con los 495 posts de `claude_labels.json` completos (batches 1-3, ver RUN-015 a RUN-019), se hizo un análisis agregado comparando `script_decision` vs `claude_decision` sobre las 5 columnas de diagnóstico (`layer1`, `layer2`, `is_public_invitation`, `is_upcoming`, `raw_date`). Precision 0.824, recall 0.858 sobre el total. Tres hallazgos concretos motivaron cambios de código; uno se descartó explícitamente.

**Cambio 1 — 18 frases de referencia en registro institucional/formal agregadas a `EVENT_REFERENCES`.**
Las 100 frases originales están casi todas en tono de invitación coloquial-comunitaria ("Te invitamos...", "Únete a nosotros..."). Los 3 falsos negativos de Capa 1 en la muestra (`iheal_creda` 0.336, `culturespaces` 0.367, `institutocervantesparis` 0.445 — a 0.005 del umbral) comparten registro sobrio/institucional (mesa redonda, coloquio, comunicado de prensa) ausente del espacio de referencia. Se agregó una sección nueva ES/FR/EN en ese registro. El cache de embeddings (`ref_embeddings.npz`) se invalida solo por hash de contenido — no requiere borrado manual.
**Impacto esperado:** bajo en volumen (recupera como máximo 3-8 posts de 495 — ver DD-035bis abajo sobre por qué no se tocó el umbral 0.45), pero dirigido: esos posts pertenecen justo al tipo de cuenta institucional que el proyecto prioriza (`manualDataCuratedAt`).

**Cambio 2 — gate determinístico `has_text_date` en `should_create`.**
14 de 22 falsos positivos (201-500) compartían un patrón: Capa 3 acepta el post como evento sin que el caption contenga ninguna fecha ancorable en el texto — el LLM razona `clean_date` "por contexto" en vez de exigir evidencia textual (listados de temporada con Pass 104infini, apertura de venta sin fecha del evento, rutas de food truck). Se agregó `has_text_date` — resultado de `extract_dates()` (ya blindado contra falsa precisión por DD-034) capturado **antes** de que Capa 3 pueda sobreescribir `event_date` con su propio `clean_date`. `should_create` ahora exige `is_event AND (not llm_ran_ok OR (is_public_invitation AND is_upcoming)) AND has_text_date`.
**Validación (sin re-correr el script):** se extrajeron las funciones `extract_dates`/`_has_day_signal`/`_DATE_RE` de forma aislada (sin cargar sentence-transformers/torch, que no están en este sandbox) y se corrieron sobre los 27 falsos positivos reales de la muestra completa (1-500, no solo 201-500). El nuevo gate habría bloqueado **15 de 27 (55.6%)**. Los 12 restantes se dividen en: casos de admisión/inscripción con fecha real en el texto (resueltos por el cambio 3, no por este gate) y un hallazgo lateral no planeado — ver nota de dígitos estilizados abajo.
**Decisión explícita tomada con Diego:** no se agregó excepción para eventos del mismo día ("hoy a las 16h") — el script corre como máximo cada 2 días, así que para cuando se procesara, un evento de "hoy" ya sería irrelevante. Esto deja sin resolver 4 de los 17 falsos negativos de la muestra (`ruedadecumbia.paris`, `theatredelaville_paris`, `cinema.lemelies.montreuil`, `alianzafrancesamanizalesoficia`) — aceptado a propósito, no es un olvido.

**Cambio 3 — instrucción negativa en el prompt de Capa 3 sobre admisión/inscripción.**
5 de los 22 falsos positivos eran cursos de idiomas con estructura fecha+hora+lugar idéntica a un evento real, concentrados en el clúster nuevo de sedes de Alianza Francesa colombianas (Pereira, Cali, Manizales) que entró al corpus en el batch 401-500. Se agregó una línea explícita al prompt: inscripciones a cursos, inicio de clases, matrículas, tests de nivel y convocatorias de candidaturas NO cuentan como `is_public_invitation`. Este cambio no se pudo validar sin correr el LLM real (no hay acceso a Groq/Cerebras desde este sandbox) — queda pendiente de confirmar en la próxima corrida real.

**Descartado — recall de Capa 1 vía bajar el umbral 0.45.**
Análisis de distribución completo (495/495 posts con `layer1`, ver gráfico de la sesión): solo 8/210 eventos reales (3.8%) caen bajo 0.45, y de los 105 posts que Capa 1 descarta, 97 (92.4%) son correctamente no-eventos. Bajar el umbral a 0.35 solo recuperaría 7 eventos reales adicionales a cambio de meter 47 no-eventos más a Capa 2/3 (razón señal/ruido 1:6.7). Se descartó por bajo retorno frente al riesgo de agravar el problema de precisión ya identificado como dominante.

**Hallazgo lateral no planeado — dígitos Unicode estilizados rompen la extracción de fecha.**
Al validar el gate `has_text_date` sobre `104paris` (post `3925185624513914500`, "15 spectacles de danse..."), se descubrió que el caption usa dígitos Unicode matemáticos en negrita (`𝟏𝟓`, `𝟏𝟎𝟒`, U+1D7EC-1D7F5) en vez de dígitos ASCII — Python los reconoce como `\d` (categoría Unicode Nd), así que `_DATE_RE`/`dateparser` los procesan igual que texto normal, lo que puede producir matches espurios ("15" de "15 spectacles" leído como posible día) en vez de fallar limpiamente. No se corrigió en esta sesión — es un problema distinto al gate de fecha (afecta la *precisión* de lo que `extract_dates()` encuentra, no si encuentra algo) y su alcance real en el corpus completo no se midió. Queda como candidato a una futura DD si se confirma que es frecuente (normalizar vía `unicodedata.normalize` antes de aplicar los regex sería el fix natural).

**Estado:** cambios 1, 2 y 3 escritos y compilados (`py_compile` OK) en `4_enrich_events_extract.py`, no comiteados a git todavía (patrón del proyecto: commit solo cuando Diego lo pide explícitamente). Pendiente de una corrida real (`--dry-run`) sobre posts nuevos para confirmar el impacto fuera de la simulación offline.

---

## DD-036 (continuación) — has_text_date movido antes de la llamada a Capa 3, y por qué NO se separó Capa 3 en dos llamadas

**Fecha:** 2026-08-12
**Contexto:** Diego propuso dividir Capa 3 en dos llamadas al LLM — una liviana de solo gate (`is_public_invitation`/`is_upcoming`) y otra de extracción completa (descripción, título, precio, etc.) solo para los que pasan el gate — con la idea de ahorrar tokens filtrando fuerte primero.
**Análisis:** sobre los 425 candidatos que llegan a Capa 3 en la muestra de 495 posts, 204 (48%) terminan siendo `EVENTO` real — es decir, casi la mitad de las veces sí hace falta la extracción completa. Separar en dos llamadas implica pagar el overhead del prompt+caption dos veces para ese 48%, mientras que el ahorro real (no pedir campos de extracción a los rechazados) es más chico que ese overhead duplicado. Con una estimación de tokens (~650 input + ~150 output por llamada actual), el punto de equilibrio para que separar valga la pena es que menos de ~25% de los candidatos terminen siendo eventos reales — muy por debajo del 48% real. **Se descartó la idea de separar Capa 3 en dos llamadas.**
**Alternativa implementada — mover el gate `has_text_date` (ya agregado en DD-036 original) de *después* de la llamada LLM a *antes*:** el gate es determinístico e independiente del LLM, así que si ya se sabe que `should_create` va a ser `False` por falta de fecha en el texto, no tiene sentido gastar la llamada. Se cambió `if is_event:` por `if is_event and has_text_date:` como condición para llamar a `llm_enrich_event()`. Verificado sobre los 425 candidatos reales: **173 (40.7%) no tienen fecha en el texto y ahora no llaman al LLM en absoluto**, sin cambiar ninguna decisión final (esos 173 ya iban a terminar en "no evento" de todas formas, por el propio gate `has_text_date` en `should_create`).
**Cambios de código:** nuevo contador `skipped_no_text_date`, nueva etiqueta de diagnóstico `"no evento (sin fecha en texto, LLM omitido)"` (distinta de `"no evento (rechazado por LLM)"`, para no confundir "el LLM dijo que no" con "nunca se le preguntó"), y nueva línea en el resumen final (`💰 Llamadas a Capa 3`) que reporta cuántas llamadas reales se hicieron vs. cuántas se habrían hecho sin el gate previo.
**Estado:** compilado y verificado (`py_compile` OK). Pendiente de confirmar el ahorro real de tokens/costo en una corrida en vivo (esta sesión no tiene acceso a Groq/Cerebras ni a Neo4j para correrlo).

---

## DD-037 — Dos bugs de extracción de fecha confirmados en la corrida real de 150 posts (RUN-020)

**Fecha:** 2026-08-12
**Contexto:** primera corrida `--dry-run` real (no simulación offline) de los cambios de DD-036, sobre 150 posts de las 43 cuentas curadas de la última tanda scrapeada. Comparación ciega Claude-vs-script: 96.7% de acuerdo (145/150), precisión 97.3% (36/37 sobre los EVENTO aceptados), recall ~90% (36/40 sobre los eventos reales según mi clasificación). Mejor que cualquier batch anterior (83-92%) — la validación en vivo confirma que DD-036 funcionó. De los 5 desacuerdos reales, 4 tienen causa de código identificada con precisión de línea.

**Bug 1 (falso positivo) — notación de temporada "26/27" leída como fecha DD/MM.**
`_DATE_RE` (línea 970) matchea `\d{1,2}[/\-]\d{1,2}` sin distinguir "temporada 26/27" de una fecha real "26/07". Post de ejemplo: `@theatrechatelet`, caption "La saison 26/27 est là ✨... Lien en bio", sin ninguna fecha real en el texto. El regex capturó `26/27`, `dateparser` lo resolvió a `2027-04-26`, y el post pasó el gate `has_text_date` como si tuviera fecha real → se creó un :Event fantasma (`cat=nulo`, sin descripción concreta). Patrón esperable en cualquier cuenta de teatro/ópera que use notación de temporada.

**Bug 2 (falsos negativos, 3 casos) — `extract_dates()` trunca a los primeros 600 caracteres, en primario (línea 1020, `text[:600]`) y en el fallback (línea 1035, mismo límite).** En captions largos (flyers multi-línea con line-up, horarios y hashtags — comunes en este corpus) la fecha real cae fuera de esa ventana y el post se descarta como "sin fecha en texto" sin llegar siquiera a Capa 3:
- `@pointephemere` — cartelera de conciertos de verano con fechas explícitas (30.06, 01.07, 07.07…) *dentro* de los primeros 600 caracteres, pero en formato `DD.MM` con punto — `_DATE_RE` solo reconoce `/` o `-` como separador, no `.`. Bug independiente del truncamiento, mismo post.
- `@academiamaritzaarizala` — taller de canto con fecha "23 juillet" explícita, pero cae en la posición 743 del caption, fuera de la ventana de 600 caracteres.
- `@saveurs_mexique` — "hasta este viernes 5 de junio" queda cortado literalmente a la mitad por el límite de 600 (el string se corta en "...5 de j", perdiendo "unio"), caso límite que ilustra el problema del corte duro en vez de un corte por límite de palabra/oración.

**Caso adicional sin causa de código clara — posible inconsistencia del LLM.** `@mestizos.folklorecolombien` publicó dos posts casi idénticos sobre el mismo evento («La Colombie, un pays qui danse», 6 de marzo, con fecha/precio/lugar explícitos en ambos). Uno fue aceptado como EVENTO; el otro, con estructura de invitación igual de clara, fue rechazado por Capa 3. No se pudo diagnosticar con evidencia de código — variabilidad del LLM entre llamadas similares, no un patrón sistemático confirmado en este batch. Se deja como observación a vigilar en próximas corridas, sin acción de código por ahora.

**Fix implementado (2026-08-12, mismo día):**
1. Ventana de `extract_dates()` ampliada de 600 a 2000 caracteres, tanto en el loop principal (`_DATE_RE.finditer`) como en el fallback (`search_dates`) — antes cada uno truncaba el texto por separado con el mismo límite de 600.
2. `.` agregado como separador válido en `_DATE_RE` (`\d{1,2}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?`). Para evitar que dateparser confunda un snippet con punto (`"01.07"`) con una hora (`01:07`) en vez de una fecha, el snippet se normaliza reemplazando `.` por `/` antes de pasarlo a `dateparser.parse()` — verificado empíricamente que sin esta normalización dateparser sí devuelve una hora en vez de una fecha para algunos casos.
3. Nuevo `_NUMERIC_DM_RE` valida que, para snippets puramente numéricos (`D[/-.]M(?:[/-.]Y)?`), el componente de "mes" sea ≤12 antes de dejarlo pasar a dateparser. Si es >12 (como en "26/27", donde dateparser trataba "27" como año de 2 dígitos y rellenaba el mes con el mes del post), el match se descarta. **Este guardrail tuvo que aplicarse dos veces** — una vez en el loop principal sobre los matches de `_DATE_RE`, y otra vez sobre el resultado de `search_dates()` en el fallback, porque `search_dates()` encuentra fechas por su cuenta (no pasa por `_DATE_RE`) y sin el segundo guardrail seguía coloándose la misma fecha inventada por esa vía — encontrado al validar el fix contra el caso real de `@theatrechatelet` (primer intento del fix falló ahí, corregido antes de dar el fix por bueno).

**Validación:** 7 casos de prueba aislados (los 4 posts reales que fallaron en RUN-020 + 3 casos que ya funcionaban bien antes, para descartar regresión) — los 7 pasan. `py_compile` OK.

**Estado:** implementado, verificado, no comiteado a git todavía (patrón del proyecto). Queda pendiente confirmar el impacto en una corrida de producción real.

---

## DD-038 — createdAt de :Event guardado como string en vez de datetime nativo, y --diag-csv ignorado en corridas reales

**Fecha:** 2026-08-12
**Contexto:** primera corrida de producción real (sin `--dry-run`) sobre las 43 cuentas curadas — 26 eventos creados, 4 enriquecidos, corrida terminó limpia (100%, resumen final impreso). Al intentar auditarla con una query Cypher (`WHERE e.createdAt >= datetime() - duration('PT2H')`), Neo4j Browser devolvió "No changes, no records" pese a que los eventos sí existían.

**Bug 1 — `e.createdAt` se guardaba como string, no como `datetime` nativo.** El candidato armaba `"createdAt": datetime.now(timezone.utc).isoformat()` (Python, string ISO) y `upsert_event()` lo escribía tal cual con `e.createdAt = $createdAt` (línea 1185). Cypher no coacciona automáticamente string↔datetime en comparaciones: `WHERE e.createdAt >= datetime() - duration(...)` se evalúa `null` fila por fila, sin error, sin resultados — la corrida quedó sin ningún rastro auditable pese a haber funcionado bien. Contraste directo con `2_build_graph.py`, que en los 12 nodos donde setea `firstSeenAt` usa siempre `datetime()` nativo de Cypher (server-side), nunca un string generado en Python — `4_enrich_events_extract.py` era la única excepción a ese patrón en todo el proyecto.
**Fix:** `e.createdAt = datetime()` (nativo, server-side), igual que `firstSeenAt`. Se eliminó el campo `createdAt` del diccionario `candidate` en Python (ya no se usa en ningún otro lado — no alimentaba el CSV de diagnóstico ni la caché de dedup, solo el write a Neo4j) y de la lista de parámetros de `upsert_event`.
**Nota para los 30 eventos ya creados con el bug:** quedaron con `createdAt` como string — para auditarlos, usar `toString(e.createdAt) STARTS WITH "<fecha>"` en vez de comparación de `datetime`, que funciona sea cual sea el tipo almacenado (string u objeto temporal). De acá en adelante todo evento nuevo va a tener `createdAt` nativo consistente.

**Bug 2 — `--diag-csv` requería `--dry-run` para exportar, sin ningún aviso si se pasaba sin él.** Línea 1716 (antes): `if dry_run and diag_csv:`. En la corrida de producción real se pasó `--diag-csv data_processed/eval_produccion_ultima_tanda.csv` esperando el mismo comparativo que se hizo con el dry-run de 150 posts — el archivo nunca se creó, sin ningún mensaje de error o advertencia que lo señalara. `diag_all`/`diag_cands` (las listas que alimentan el CSV) se llenan de forma incondicional en el loop principal, sin ningún gate de `dry_run` — no había ninguna razón real para que el export dependiera de ese flag.
**Fix:** `if diag_csv:` — exporta siempre que se pase el flag, sea dry-run o corrida real. Sin costo ni riesgo adicional (los datos ya estaban disponibles en memoria en ambos modos).

**Por qué importa:** sin este fix, cualquier corrida de producción futura queda sin comparativo posible después del hecho — ni CSV, ni consulta Cypher confiable por fecha. Ambos bugs comparten la misma raíz: falta de paridad entre el modo dry-run (bien instrumentado, pensado para auditoría) y el modo real (pensado solo para escribir, sin pensar en poder revisar después). Este fix cierra esa brecha para que cualquier corrida — real o de prueba — quede siempre auditable.

**Estado:** implementado, compilado (`py_compile` OK), no comiteado a git todavía (patrón del proyecto). Sin validar todavía en una corrida real posterior al fix — la próxima corrida de producción debería confirmar que el CSV se exporta y que `e.createdAt` queda como tipo `datetime` nativo (verificable con `RETURN apoc.meta.type(e.createdAt)` si hay APOC, o simplemente confirmando que `e.createdAt >= datetime() - duration('PT1H')` sí devuelve filas).

---

## DD-039 — Dos bugs más encontrados al auditar los 90 eventos escritos en producción, con fix aplicado

**Fecha:** 2026-08-12
**Contexto:** con el fix de DD-038 (`toString(e.createdAt) STARTS WITH ...`) ya se pudo traer los eventos escritos en las corridas de producción de hoy (90 en total, sumando las 3 corridas — la de 50 posts, la que cortó con `SessionExpired` en el post 232, y la final). Revisión manual encontró dos patrones de falso positivo, distintos entre sí y de todo lo visto en DD-037. Se confirmó primero que `4_enrich_events_resolve.py` (Fase 4-C, deduplicación) no resuelve ninguno de los dos — solo fusiona duplicados por ubicación+fecha+similitud de embedding, sin tocar `category`/`type` ni revalidar `eventDate`.

**Bug A — `should_create` no filtraba `category="nulo"`.** 3 eventos reales (`evt_50f63ba74790` deporte/high-diving, `evt_0f4298aa9df7` reapertura de café, `evt_5646210a4124` feria comercial) fueron tipificados por la propia Capa 3 con una etiqueta de `_LABEL_META` que mapea a `category="nulo"` — es decir, el LLM dijo explícitamente "esto no es un evento cultural real" — y aun así se crearon como `:Event`, porque `should_create` (línea ~1551) nunca miraba `category`, solo `is_public_invitation`/`is_upcoming`/`has_text_date`. `penalty` sí caía a 0.0 vía `_PEN_MAP` (por eso los 3 tenían `eventScore=0.0` — la señal estaba ahí, solo que nada la usaba para bloquear la creación).
**Fix:** se agregó `and category not in NULL_CATS` a `should_create`. Nueva etiqueta de diagnóstico `"no evento (categoría nula, DD-039)"`, distinta de `"rechazado por LLM"` (que significa que las banderas de invitación vinieron en `False` — acá pueden venir en `True` y aun así bloquearse por categoría). `category="sin_clasificar"` (el LLM no tipificó, distinto de tipificar explícitamente como no-cultural) NO bloquea — verificado en pruebas aisladas (6/6 casos).

**Bug B — el clamp de fecha (`EVENT_DATE_CLAMP_DAYS=1095`) no atajó 3 fechas absurdamente lejanas.** `evt_84ac6431ef67` (Latino Graff) quedó con `eventDate=2036-08-05` — el texto dice "10 años de Latino Graff" (aniversario, no fecha). `evt_f7218e3f0ed0` (Institut du monde arabe) quedó con `2052-08-04` — el texto dice "menores de 26 años" (edad). `evt_2212c8cb309b` (concierto flamenco) quedó con `2090-07-01` — el texto dice "dura 90 minutos" (duración). El resumen de consola de la corrida reportó `Fechas clampeadas: 0`, cuando debería haber sido ≥3. Causa confirmada en código (línea 1513, antes del fix): el `try/except (ValueError, TypeError): pass` envolvía TANTO el parseo de `ed` (la fecha del LLM) COMO el de `pd` (el timestamp del post) — si cualquiera de los dos fallaba al parsear, la excepción abortaba todo el bloque sin aplicar el clamp, dejando pasar la fecha del LLM sin ninguna validación. No se pudo confirmar con certeza total cuál de los dos (`ed` o `pd`) fue el que falló en estos 3 casos específicos (sin acceso al `post["timestamp"]` original de cada uno), pero el fix cierra el hueco para ambos casos por igual.
**Fix:** se separó el try/except en dos partes independientes. Si `ed` (la fecha del LLM) no parsea como ISO válido en absoluto, se descarta directo (`event_date = None`, cuenta como clampeado) — no hay ninguna razón para confiar en algo que ni siquiera es una fecha. Si `pd` (el timestamp del post) falla al parsear, se usa `STUDY_CUTOFF` como ancla de respaldo en vez de abortar todo el chequeo — así el clamp sigue aplicando aunque falte el ancla real. Verificado con 8 casos aislados (incluidos 2 eventos legítimos de 2027 con más de 1095 días de anticipación, para confirmar que el fix no los clampea de más).

**Estado:** ambos fixes implementados, compilados (`py_compile` OK), verificados en aislamiento (6/6 y 8/8 casos), no comiteados a git todavía. Los 6 eventos ya escritos en Neo4j con estos bugs (3 de cada patrón) quedan pendientes de borrado manual — ver query en el mensaje a Diego. Sin validar todavía en una corrida real posterior al fix.

---

## DD-040 — Rediseño de 4_enrich_events_resolve.py: embeddings globales en vez de agrupar por locationName

**Fecha:** 2026-08-12 (continúa al 2026-08-13)
**Contexto:** al revisar los 90 eventos de la corrida de producción, se identificaron dos pares de duplicados obvios que el resolver (sin correr todavía esta sesión) no iba a poder fusionar: "Los Tucanes de Tijuana en concierto en París" (`pac_colibri`, 2026-07-07) con `locationName` escrito distinto entre los dos posts ("La Palmeraie, 20 Rue..." vs "20 Rue..."), y "L'Astrologue ou les Faux Présages" (`sorbonne_lettres_culture`, 25-28 junio) donde uno de los dos posts no tiene `locationName` en absoluto.
**Diagnóstico:** el algoritmo anterior agrupaba por `locationName` normalizado (lowercase + unidecode) y solo comparaba pares DENTRO del mismo grupo — dos eventos con el mismo venue escrito distinto caen en grupos de string distintos y nunca se comparan; un evento sin `locationName` cae en el grupo `""` y nunca se compara contra uno que sí lo tiene. La fecha y la similitud de embedding nunca tenían oportunidad de opinar en estos casos, porque el filtro de ubicación los descartaba antes.
**Decisión (debatida con Diego):** invertir el orden — comparar TODO par de eventos por similitud de embedding primero (matriz de coseno vectorizada con numpy sobre los ~700 eventos del grafo, ~250k pares, trivial en tiempo), y usar fecha como confirmación obligatoria en vez de filtro primario. Se descartó tratar `locationName` como embedding (Diego señaló correctamente que un embedding de texto captura similitud léxica, no lógica geográfica real — "5 Rue de Rivoli" y "7 Rue de Rivoli" no necesariamente embeben cerca, mientras que dos teatros distintos que comparten palabras como "Théâtre de/du" sí podrían mostrar similitud alta sin ser el mismo lugar). `locationName` pasa a ser dato informativo en los logs, no un filtro.
**Nuevo criterio:**
  - Si ambas fechas existen: deben estar dentro de `±date_window` días (3 por defecto, sin excepción, igual que antes) Y similitud ≥ `threshold` (0.75, sin cambios).
  - Si falta alguna fecha: similitud ≥ `threshold_no_date` (nuevo, default 0.85) — más exigente, compensa la falta de corroboración por fecha.
  - Se auditó la riqueza real de `locationName` en los 90 eventos de la corrida: ~20% con dirección completa, ~55% solo nombre de venue sin dirección, ~10% solo ciudad/país (un caso literalmente `locationName="Europa"`), ~15% vacío — confirma que el string de ubicación es demasiado inconsistente para usarlo como filtro confiable.
**Nueva instrumentación de calibración (dry-run):** además de la muestra existente de pares con fecha+ubicación ok pero similitud insuficiente, se agregó una segunda muestra simétrica — pares con similitud ≥ `threshold` pero fecha fuera de la ventana de `date_window` días. Motivo: varios eventos del corpus son rangos largos (MIRA Art Fair 12-15 nov, Gaîté l'été 30 jun-2 ago, ManiFeste Academy 12 días) — si dos posts sobre el mismo evento anclan a extremos distintos del rango, 3 días podría quedarse corto. En vez de ensanchar la ventana a ciegas, esta muestra da evidencia real para decidir si hace falta.
**Cambios de código:** removidos `normalize_loc()`, `cosine_sim()` (reemplazado por matriz vectorizada), `LOCATION_GROUP_MAX_FOR_EVIDENCE`, y los imports que quedaron sin uso (`defaultdict`, `unidecode`, `scipy.spatial.distance.cosine`) — agregado `numpy`. `main()` expone el nuevo `--threshold-no-date`.
**Validación:** 6 casos aislados con vectores sintéticos (similitud coseno controlada, sin depender de sentence-transformers en este sandbox) — replican exactamente los dos pares reales (Tucanes, Astrologue: ahora sí fusionan), un caso de fechas lejanas con alta similitud (correctamente rechazado pese al mismo teatro), y el umbral compensatorio sin fecha en ambas direcciones (rechaza similitud débil, acepta similitud casi idéntica). `py_compile` OK, sin referencias residuales a las funciones removidas.
**Estado:** implementado, compilado, verificado en aislamiento con datos sintéticos — sin validar todavía contra los ~700 eventos reales en Neo4j (pendiente `--dry-run` de Diego). No comiteado a git todavía.

---

## DD-041 — Guardrail de conflicto geográfico + títulos en el log de fusión

**Fecha:** 2026-08-13
**Contexto:** primer `--dry-run` real del rediseño DD-040 sobre los 664 eventos del grafo. Confirmó lo esperado (el par de L'Astrologue, antes bloqueado por falta de `locationName` en un lado, se fusionó correctamente) pero también expuso un problema nuevo: sin el filtro de ubicación, aparecieron fusiones geográficamente contradictorias — `'París'/'Colombia'` (sim=0.770), `'Ecuador'/'Paris'` (sim=0.754), `'París'/'Madrid'` (sim=0.771), `'Portugal'/'@osullivans_bastille'` (sim=0.753). Un evento (`evt_10f3fc744b97`) absorbió 4 posts de fiestas/encuentros de la diáspora en ubicaciones distintas — posts genéricos de comunidad comparten vocabulario aunque describan eventos distintos en lugares distintos. El `threshold=0.75` estaba, en la práctica, calibrado contando con que el filtro de ubicación ya eliminaba estos casos.
**Fix — `geo_conflict()`:** gazetteer chico (no exhaustivo, no es un geocoder real) de ~35 nombres de país (ES/FR/EN) y ~20 ciudades/barrios frecuentes en el corpus, mapeados a código de país, tomados directamente de los casos reales que aparecieron en la corrida. Extrae los países mencionados en cada `locationName` vía match de palabra completa (normalizando separadores no alfabéticos — `@`, `_`, etc. — a espacios, para que "bastille" se reconozca dentro de "@osullivans_bastille"). Bloquea la fusión solo si AMBOS lados mencionan un país reconocido Y no se solapan en absoluto — jerarquía país↔ciudad incluida (Francia+Paris no es conflicto), y evidencia insuficiente en cualquiera de los dos lados nunca bloquea (permisivo por diseño, para no reintroducir el problema original de exigir match exacto). Nueva muestra de calibración (`🌍 MUESTRA — bloqueados por conflicto geográfico`) para revisar si el gazetteer está bloqueando algo que en realidad sí era el mismo evento.
**Fix secundario:** la línea `[dry-run] MERGE` no incluía títulos, solo ubicación y fecha — imposible auditar visualmente si una fusión tenía sentido sin ellos. Se agregaron (los datos ya estaban disponibles vía `load_all_events()`, que ya seleccionaba `e.title`).
**Validación:** 14 casos aislados con el gazetteer real (sin depender de Neo4j) — 13 correctos, 1 con expectativa de prueba equivocada de mi parte (no un bug: "Embajada de Costa Rica en Francia" reconoce ambos países, y contra una ubicación sin ningún país reconocido el diseño permisivo correctamente no bloquea). `py_compile` OK.
**Estado:** implementado, compilado, verificado en aislamiento. Pendiente confirmar en una corrida `--dry-run` real que el guardrail bloquea los 4 casos concretos que lo motivaron sin bloquear las fusiones legítimas ya confirmadas (L'Astrologue, direcciones idénticas con distinto formato). No comiteado a git todavía.

## DD-042 — `eventArtTags`: tema artístico por evento, generado por el LLM (Capa 3)

**Fecha:** 2026-08-13
**Contexto:** surgió mientras se diseñaba el menú del rediseño del dashboard (`docs/dashboard_redesign_proposal.md`, sección 6). Se probó usar `Account.artType` (curación manual, heredado en `Event.artType`) como eje principal del menú — dio pie a un hallazgo: `artType` describe la cuenta, no el evento. Cuentas-sede omnívoras (La Villette, Gaîté Lyrique) traen `artType="Música, Danza, Circo, Teatro, Artes visuales"` pegados, y ese string se hereda igual en TODOS sus eventos sin importar si un evento puntual es en realidad un concierto o una proyección de cine. Muestra real: de 25 eventos con `artType` conteniendo "Artes visuales", solo 10 (40%) tenían `category='visual'` — el resto eran música/cine/danza mal etiquetados por herencia de cuenta. Además, ni `artType` ni `category` cubren temas reales del proyecto como Literatura o Circo (no existen en `_LABEL_META`, los 16 tipos fijos del extractor).
**Decisión:** `Account.artType`/`Event.artType` (heredado) se mantienen sin cambios — siguen siendo útiles como señal de qué hace una cuenta en general. Se agrega un campo NUEVO y distinto, `Event.eventArtTags`, generado por el LLM de Capa 3 **por evento puntual** (no heredado de la cuenta): lista de 1-3 tags cortos describiendo la disciplina/medio artístico específico de ESE evento — más rico/granular que `type` (16 opciones fijas), pero más controlado que el `artType` de cuenta.
**Fix del problema de parseo (aprendido de `artType`):** en vez de texto libre con comas (que se rompe cuando hay descripciones entre paréntesis — el bug que motivó esta decisión), `art_tags` es un array JSON real desde que sale del LLM, y `_clean_art_tags()` valida cada tag: descarta cualquiera con paréntesis, comas internas, o más de 40 caracteres, y topea a 3 tags. Vocabulario sugerido en el prompt (Música, Danza, Teatro, Circo, Literatura, Cine, Fotografía, Artes visuales, Moda, Gastronomía, Arquitectura, Cómic) pero el LLM puede proponer uno nuevo corto si ninguno aplica — rico pero clasificable, como pidió Diego.
**Cambios en `4_enrich_events_extract.py`:**
- `_llm_schema_hint()`: agregado `"art_tags"` al JSON pedido al LLM, con instrucciones explícitas contra paréntesis/comas internas.
- `_LLM_DEFAULTS["art_tags"] = []` (antes no existía).
- Nueva función `_clean_art_tags()`: valida/normaliza la respuesta del LLM.
- `_extract_llm_fields()`: usa `_clean_art_tags()` sobre `data.get("art_tags")`.
- Loop principal: nueva variable `llm_art_tags`, poblada solo cuando Capa 3 corre (igual que `llm_title`, `llm_price_range`, etc.).
- `record` (diagnóstico dry-run) y `diag_csv` `fieldnames`: incluyen `art_tags` para poder auditar la calidad de los tags antes de confiar en ellos.
- `candidate["eventArtTags"]` y `upsert_event()`: nuevo `e.eventArtTags = $eventArtTags` en el `SET` de creación del nodo — **solo se escribe al CREAR el evento**, igual que el resto de campos de Capa 3 (no se sobreescribe al enriquecer/fusionar).
**Validación:** `py_compile` OK. `_clean_art_tags()` probado en aislado, 11/11 casos (tags válidos, `None`, string en vez de lista, paréntesis+coma, coma suelta dentro del tag, tope de 3, string vacío tras strip, elemento no-string mezclado, tag demasiado largo).
**Alcance — importante:** este campo es **prospectivo únicamente**. Los 664 eventos ya existentes en Neo4j NO tienen `eventArtTags` (se crearon antes de este cambio) y no se van a backfillear automáticamente — eso requeriría volver a llamar a Capa 3 sobre cada uno (costo de tokens, no trivial). Si en algún momento se quiere ese backfill, es una tarea aparte, explícita, no implícita en este fix.
**Pendiente:** correr `--dry-run` sobre una muestra nueva y revisar la columna `art_tags` del CSV de diagnóstico antes de confiar en esto para producción — no se ha validado contra output real del LLM todavía, solo la función de limpieza en aislado.

---

## DD-043 — `5_export_dashboard_data.py`: exportación estática para el sitio nuevo, sitio estático sin backend

**Fecha:** 2026-08-13
**Contexto:** decidido reemplazar el dashboard Dash por un sitio de descubrimiento (`docs/dashboard_redesign_proposal.md`). Sin acceso de red a Neo4j Aura desde el entorno donde se construye el sitio, y dataset chico (664 eventos, ~1-2MB en JSON) — un sitio 100% estático (JSON + HTML/CSS/JS, sin servidor, sin Neo4j en producción) es la opción más simple y barata (ver comparación de hosting: GitHub Pages vs Cloudflare Pages vs Netlify vs Vercel, GitHub Pages elegido para arrancar).
**Qué hace el script:** trae eventos válidos + geocodificados (mismo criterio que la sección 6.3 de la propuesta: `isPublicInvitation`/`isUpcoming`=true, fecha real, `:LOCATED_AT` con `lat`/`lon`) y cuentas curadas/con métricas de grafo, en dos queries. Calcula sobre el dataset completo los sub-scores de ranking que necesitan contexto de percentil — `qScore` (calidad de detección), `aScore` (autoridad de la fuente vía PageRank/betweenness/followers/participación), `bScore` (resonancia social) — y un `penaltyMultiplier` (político ×0.55, confianza baja ×0.80, gratis ×1.05). **A propósito NO calcula** el componente T (proximidad temporal, depende de "hoy") ni C (contexto de sesión, depende del navegador de cada visitante) — esos se computan en el cliente en cada visita, calcularlos acá los dejaría fechados a la última exportación. También calcula similitud coseno vectorizada (mismo patrón que `4_enrich_events_resolve.py`) y guarda los 5 vecinos más cercanos por evento como `similarEventIds` — el embedding de 384 floats se descarta después de usarse, nunca viaja al JSON final (ver nota de la propuesta sobre no exponer embeddings al cliente).
**No escribe nada en Neo4j** — solo lectura, se puede correr las veces que haga falta para refrescar `site/data.json` después de cada corrida de extracción/resolver.
**Validación:** `py_compile` OK. `pctl_rank()` (percentiles con empates y valores `None`), `is_free()` (8/8 casos: "Gratis", "Entrada libre", "Accès libre", "Free entry" vs precios reales) y el pipeline completo de `compute_similar_events()`/`compute_ranking_subscores()` probados en aislado con datos sintéticos — confirmado que político+confianza baja compone penalizaciones (0.55×0.80), que gratis da el bonus (×1.05), y que un evento sin embedding no rompe nada (`similarEventIds=[]`).
**Pendiente:** correr contra Neo4j real (Diego, sin acceso desde este entorno) y confirmar tamaño real del JSON y que las cuentas se linkeen bien por `sourceAuthor`/`username`.

---

## DD-044 — `site/`: sitio estático (HTML/CSS/JS vanilla), sin build step, menú 100% dinámico

**Fecha:** 2026-08-14
**Contexto:** implementación real del rediseño (`docs/dashboard_redesign_proposal.md`) sobre `5_export_dashboard_data.py` (DD-043). Hosting elegido: Cloudflare Pages (bandwidth ilimitado, deploy directo desde el repo de GitHub ya existente).
**Qué es:** `site/index.html` + `site/style.css` + `site/app.js` + `site/i18n.js` (ES/FR) — vanilla JS, sin framework ni build step, para que el deploy sea literalmente "conectar el repo" sin configurar nada. Lee `site/data.json` (mismo archivo que escribe el export). Reusa la paleta de colores/tipografías del dashboard Dash viejo (`dash_common.py`) para continuidad visual.
**Menú 100% dinámico (decisión 2026-08-14):** sin lista fija de familias — el eje "QUÉ" se arma en cada carga a partir de la unión de `category` (mapeado a label legible) y `eventArtTags` (DD-042) presentes en los datos, con conteo real, ordenado por volumen. La curación de fondo (qué cuentas se scrapean) es lo que le da forma al menú, no una regla editorial en el código — así lo pidió Diego, dado que la curación de cuentas ya prioriza lo que le interesa.
**Ranking en dos tiempos:** `qScore`/`aScore`/`bScore`/`penaltyMultiplier` llegan precalculados del export (necesitan contexto de todo el dataset — percentiles). `T` (proximidad temporal) y `C` (contexto de sesión, `localStorage` sin login, ver propuesta sección 3.5) se calculan en el navegador en cada visita, porque dependen de "hoy" y de las preferencias de esa persona — precalcularlos en el export los dejaría fechados a la última exportación.
**Validación:** intenté correr un smoke test con jsdom (headless) pero el entorno de sandbox donde construyo el sitio tiene el mount de `hub-cultural-du/` sincronizado con la máquina real de Diego, y operaciones de filesystem pesadas (miles de archivos de `node_modules`) colgaban indefinidamente — no es un problema del sitio, es fricción del entorno de desarrollo. Pivoté a extraer y probar en aislado, con Node puro (sin DOM), toda la lógica pura de `app.js` (`eventThemes`, `whenBucket`/`computeT`, `applyFilters`, `computeC`, `relevance`, `diversify`) contra 6 eventos sintéticos con fechas relativas a hoy reales — confirmado: filtro geográfico, filtro de tema dinámico (incluido "Literatura", que viene de `eventArtTags` y no existe en `category`), filtro de gratis, exclusión correcta de eventos pasados, ranking con orden esperado, y `computeC` reaccionando bien a preferencias de sesión simuladas. Complementado con verificación cruzada de que cada `getElementById` de `app.js` tiene su `id` en `index.html`, y que cada `data-i18n` del HTML tiene su clave en `i18n.js`. **No probado:** renderizado real en un navegador (DOM real, CSS real) — recomendado que Diego lo abra localmente antes de conectar Cloudflare.
**Nota de limpieza:** al intentar el smoke test con jsdom se instaló un `node_modules` (1834 archivos) dentro de `site/` por accidente — el `rm -rf` normal falló con "Operation not permitted" en el mount sincronizado; se resolvió pidiendo permiso vía `allow_cowork_file_delete` y ya está limpio. Mencionado acá por transparencia, no por ser una decisión de arquitectura.
**Pendiente:** Diego corre `python 5_export_dashboard_data.py` (escribe directo a `site/data.json`, pisando el archivo sintético de prueba), abre `site/index.html` local para revisar visualmente, y luego conecta el repo a Cloudflare Pages.

---

## DD-045 — Validación en vivo del sitio de descubrimiento: bugs encontrados, cambio de alcance del proyecto, y lista de pendientes antes del próximo scrapeo

**Fecha:** 2026-08-14/15
**Contexto:** Diego corrió `5_export_dashboard_data.py` contra Neo4j real (170 eventos, 200 cuentas — pisó el `site/data.json` sintético que dejaba DD-044), conectó el repo a Cloudflare mediante **Workers Builds** (no el flujo clásico de Pages documentado en DD-044 — Cloudflare unificó Workers y Pages; el deploy command por defecto es `npx wrangler deploy`, que requiere un `site/wrangler.jsonc` con `{"assets": {"directory": "."}}` y "Root directory" = `site/`), y publicó en `https://hub-cultural.diegomerchanm.workers.dev/`. Esta fue la primera revisión visual real del sitio (la que DD-044 dejaba pendiente) y encontró varios problemas concretos, con causa raíz confirmada leyendo el código, no solo observados en pantalla.

**Bugs diagnosticados:**
1. **~~Filtro "Próximos" no filtra nada~~ — CORRECCIÓN 2026-08-15: falso positivo.** El diagnóstico original decía que el bloque `if (STATE.when !== "upcoming")` en `site/app.js` dejaba pasar eventos pasados cuando el filtro activo era "upcoming". Al releer la función completa (la primera lectura se cortó en la línea 152 y no llegó al `else`), se confirmó que sí existe un `else` que hace `if (d === null || d < 0) return false` — el filtro excluye correctamente eventos pasados, y así estuvo desde el primer commit del sitio (verificado con `git log --follow -p`, un solo commit para `app.js`). **No hay bug acá.** Si Diego sigue viendo el evento de Manizales (u otro pasado) bajo "Próximos" en el sitio en vivo, hay que reproducirlo de nuevo con capturas antes de asumir causa — candidatos a revisar: caché del navegador/Cloudflare, o que el evento en cuestión aparezca en otra sección/filtro y no en "Próximos" propiamente.
2. **Coordenadas falsas por fallback de geocodificación** — `4_enrich_locations.py` calcula `geocodeConfidence` (`"exact"` / `"city_combined"` / `"city_hint_only"`) pero `5_export_dashboard_data.py` solo filtra por `lat`/`lon IS NOT NULL`, ignorando esa señal. Eventos de Manizales/Medellín/Pereira aparecieron con coordenadas de París (48.8588897, 2.320041) porque el geocoder cayó al peor tier (`city_hint_only`, que geocodifica literalmente el `--city-hint` global `"Paris, France"` en vez de la ubicación real del evento) y esa coordenada pasó como si fuera válida. Fix propuesto: excluir `city_hint_only` en el export — no requiere LLM ni geocodificación nueva, solo usar una señal que ya se calcula y hoy se descarta.
3. **Cuentas fuera de alcance geográfico**: `alianzafrancesamanizalesoficia`, `alianzafrancesademedellin`, `alianza_francesa_de_pereira` — instituciones físicamente en Colombia, sin `manualDataCuratedAt`, coladas vía descubrimiento automático `RELATED_TO`.
4. **Fecha imprecisa**: al menos un evento con año mal inferido por el LLM (2027 en vez de 2026) a partir de una caption que solo mencionaba el mes. Confirma que fecha/ubicación es la mayor debilidad actual del pipeline.
5. **No existe mapa clickeable** en el sitio — quedó solo como mockup de diseño; DD-044 nunca lo construyó.
6. **No hay banco de imágenes** — placeholders genéricos únicamente.
7. **Traducción ES/FR incompleta** — `i18n.js` solo cubre el chrome de la interfaz, no el contenido de los eventos (título/descripción quedan solo en el idioma original de la caption).

**Cambio de alcance del proyecto (ampliación adicional a la de DD-041/ranking):** el criterio de inclusión deja de ser "diáspora latinoamericana" y pasa a ser **relevancia cultural**, con prioridad a fotografía, cine, literatura, teatro, y eventos de comunidades locales — de cualquier nacionalidad. Ya incorporado en `thesis/main.tex` (§1.1, §6.2); se registra también acá porque afecta directamente el punto 3 de la lista de pendientes (esas cuentas de Alianza Francesa en Colombia no se descartan por ser "no latinoamericanas", sino por estar fuera del área geográfica del proyecto).

**Orden de trabajo acordado para antes del próximo scrapeo:**
1. ~~Arreglar filtro "Próximos"~~ — no era un bug real, ver corrección arriba. **Cerrado sin cambio de código.**
2. ✅ **Hecho, en tres intentos (2026-08-15) — el tercero funciona sin depender de `geocodeConfidence`.**
   - Intento 1: `<> 'city_hint_only'` — insuficiente, persistían 92 eventos en 4 coordenadas "promiscuas" (una coordenada compartida por 20-33 `locationName` completamente distintos: `@dichaparis`, "Teatro Colsubsidio Roberto Arias Pérez" en Medellín, handles de Instagram sueltos).
   - Intento 2 (allowlist `IN ['exact', 'city_combined']`): reveló, vía el warning de Neo4j al correr la query ("property key does not exist"), que **`geocodeConfidence` nunca se escribió en ningún nodo de la base** — no es que esté `null` en algunos, el campo no existe en absoluto. Causa: `4_enrich_locations.py` es idempotente por `lat IS NULL` como criterio de "falta geocodificar"; como todos los `Location` ya tenían `lat`/`lon` de antes de que se agregara el campo `geocodeConfidence` al script, nunca se reprocesaron y el campo quedó sin poblar en la práctica (bug real en `4_enrich_locations.py`, documentado acá, no arreglado — requeriría relajar la condición de idempotencia para un backfill puntual). Este intento devolvía 0 eventos.
   - **Intento 3 (el que quedó):** se retiró el filtro de `geocodeConfidence` de la query Cypher y se agregó `_filter_fallback_coordinates()` en Python, en `5_export_dashboard_data.py`, que no depende de ninguna propiedad de Neo4j: agrupa eventos por coordenada redondeada a 4 decimales y excluye cualquier grupo con 3+ `locationName` distintos — la señal empírica confirmada (una coordenada real y compartida tiene el mismo nombre de lugar repetido entre eventos de la misma cuenta; una coordenada de fallback junta nombres sin relación alguna). Validado en aislado con datos sintéticos: excluye el grupo de nombres heterogéneos, conserva un venue real repetido 3 veces con el mismo nombre.
   - **Resultado real (2026-08-15, corrida de Diego):** 170 → 71 eventos. 4 coordenadas de fallback detectadas y excluidas (74 eventos únicos: 55+23+10+4 filas, con solapamiento de ids duplicados — ver punto siguiente).
   - **Bug nuevo descubierto al validar este fix:** 5 eventos traían 2-4 filas en `EVENTS_QUERY` (el mismo `id`, mismo `locationName`) pero con coordenadas **completamente distintas entre sí** — ej. "Café Otraparte" resolviendo a la vez en Colombia y en España, "Honduras" resolviendo a Honduras, México y medio del océano Atlántico Sur. No es duplicación cartesiana inofensiva (filas idénticas): son geocodificaciones en conflicto real, probablemente de múltiples `:Location` nodes distintos apuntando al mismo `:Event` vía `:LOCATED_AT`. Se agregó `_dedupe_conflicting_locations()` (corre antes que `_filter_fallback_coordinates()`): si las filas de un mismo id no coinciden en coordenada, se descarta el evento completo (no se adivina cuál de las N es la correcta); si coinciden, se colapsa a una sola fila. Validado en aislado.
   - Pendiente (aparte, no bloqueante): investigar en el grafo por qué algunos `:Event` tienen más de un `:LOCATED_AT` con `:Location` distintos — probablemente un bug de `2_build_graph.py` o del resolver que no está garantizando una sola relación de ubicación por evento.

3. ✅ **Hecho (2026-08-15) — cambio de producto, no solo de código:** Diego identificó que se estaba conflando "¿el evento merece aparecer en el sitio?" con "¿la geocodificación es confiable?", cuando son cosas independientes — el sitio siempre mostró (y sigue mostrando) el texto de ubicación tal cual lo extrajo el LLM (`exactAddress`/`locationName`/`cityName`), nunca nada derivado de Nominatim; el usuario verifica contra la publicación original. Implementado con un agente Opus (verificado después por mi cuenta, no solo su reporte): `EVENTS_QUERY` ya no exige `:Location` con `lat`/`lon` — exige solo que exista algún texto de ubicación. `_dedupe_conflicting_locations()` y `_filter_fallback_coordinates()` ya no excluyen eventos: les setean `lat`/`lon = null` (sin pin de mapa futuro) pero los mantienen en la exportación. Corrida real de Diego contra Neo4j: **222 filas → 183 eventos con texto de ubicación (57 con coordenada confiable, 126 sin pin pero visibles)** — vs. las 71 de la vuelta anterior, que descartaba de más.
   - Pendiente: revisar visualmente el sitio con estos 183 eventos — el filtro de texto es más laxo que el viejo filtro implícito por geocodificación, así que podría dejar pasar basura conocida (emoji o texto no-geográfico como único `locationName`, ver limpieza pendiente de eventos legacy más abajo en este documento).

5. ✅ **Hecho (2026-08-15) — punto 3 del punchlist original: cuentas fuera de alcance.** Confirmadas 4 cuentas de Alianza Francesa físicamente en Colombia (`alianzafrancesamanizalesoficia`, `alianzafrancesademedellin`, `alianza_francesa_de_pereira`, `alianzafrancesacali`) — ninguna en `config/seeds_*.json` (se colaron por descubrimiento automático `RELATED_TO`, no por curación). Nuevo `exclude_accounts.py`: lee `config/excluded_accounts.json` (username + razón por cuenta) y tagea los `:Account` correspondientes con `outOfScope=true`/`outOfScopeReason`/`outOfScopeAt` — **no borra nada**, mismo espíritu que `cleanup_legacy_accounts.py` (tagear antes de borrar). `--dry-run` con `ROLLBACK`, reporta cuántos posts/eventos quedarían asociados sin tocarlos. `5_export_dashboard_data.py` actualizado: `EVENTS_QUERY` ahora hace `OPTIONAL MATCH (src:Account {username: e.sourceAuthor})` y excluye si `src.outOfScope=true`; `ACCOUNTS_QUERY` excluye directamente por la misma condición.
   - **Limitación conocida, documentada a propósito:** esto no evita que la cuenta reaparezca como `:Account` nuevo si `2_build_graph.py` la redescubre de nuevo vía `RELATED_TO` desde otra cuenta — ese nodo nuevo no heredaría el tag. Blindar la ingestión para que consulte `config/excluded_accounts.json` antes de crear el nodo queda pendiente, fuera de alcance de este pedido puntual.
   - Pendiente: Diego corre `python exclude_accounts.py --dry-run` para confirmar conteos, después `python exclude_accounts.py` (sin dry-run) para taguear de verdad, y por último `python 5_export_dashboard_data.py` de nuevo para que `site/data.json` refleje la exclusión.

6. ✅ **Hecho (2026-08-15) — rediseño de `4_enrich_locations.py` en la fuente, no más parches en el export.** Antes de tocar el LLM (la idea original de contexto de cuenta + arbitraje sobre candidatos), se encontró que la mejora que se iba a proponer **ya existía en el código**: un hint de ciudad por-Location, tomado del `cityName` del propio evento que originó esa Location, en vez de un `--city-hint` global fijo (comentario en el código cita "DD-033 update 6", aunque esa entrada nunca quedó documentada acá como tal). El problema no era la lógica — era que **nunca se aplicó a los datos existentes**: el script solo procesa `WHERE l.lat IS NULL`, así que cualquier Location ya geocodificada (aunque mal, con lógica vieja) nunca se reprocesa. Mismo patrón de idempotencia-que-nunca-se-revisita ya encontrado con `geocodeConfidence` (punto 2) — tercera vez que aparece esta clase de bug en el mismo script.
   - **Fix 1 — backfill automático:** `WHERE l.lat IS NULL OR l.geocodeConfidence IS NULL` en vez de solo `lat IS NULL`. Como `geocodeConfidence` nunca se pobló en la práctica, esto fuerza un reprocesamiento único de todo lo existente bajo la lógica actual; una vez que esta corrida deje `geocodeConfidence` poblado en todos los nodos, las corridas siguientes vuelven a ser tan baratas como antes (auto-limitante, no hace falta un flag nuevo ni acordarse de sacarlo después).
   - **Fix 2 — se retira el tier `city_hint_only`** de `geocode_location()`: era el que, cuando el nombre exacto y nombre+ciudad fallaban, geocodificaba literalmente el hint solo (ej. "Paris, France") y lo devolvía como si fuera un resultado válido — la causa raíz confirmada de la contaminación masiva de coordenadas encontrada en los puntos 2 y 3. Ahora, si ambos intentos fallan, la Location queda sin lat/lon (`None`) — mismo criterio que ya usa el prompt del LLM para `city`/`exact_address` ("preferible null a una ubicación adivinada").
   - Validado en aislado con un geocoder falso (sin red real): confirmado que el tier "exact" resuelve en 1 llamada, "city_combined" en 2, y que cuando ambos fallan la función devuelve `None` **sin intentar nunca** geocodificar el hint solo.
   - **Alcance deliberadamente recortado:** la idea original también incluía que un LLM arbitre entre varios candidatos reales de Nominatim en vez de quedarse con el primero. Se decidió no implementarla todavía — los dos fixes de arriba ya atacan la causa raíz confirmada con evidencia real, sin costo de LLM nuevo ni riesgo adicional. Queda como mejora futura a evaluar después de ver el resultado del backfill.
   - Pendiente: Diego corre `python 4_enrich_locations.py` (sin `--dry-run` para escribir de verdad; con `--dry-run` primero para ver cuántas Location entran al backfill) y después `python 5_export_dashboard_data.py` de nuevo — esperable que la cantidad de eventos con pin de mapa confiable suba y que las coordenadas "promiscuas" que detecta el export bajen o desaparezcan.
   - **Auditoría del patrón en todo el pipeline (2026-08-17), antes de correr el backfill.** Diego pidió revisar si el patrón "guard de idempotencia por `campo IS NULL` + lógica de procesamiento que mejoró después" está en otros scripts. Revisados los 13 scripts activos, solo con lectura de código (sin acceso a Neo4j desde el entorno del agente — ningún conteo de abajo está confirmado contra datos en vivo).
     - **Instancia mayor, NO tocada (requiere decisión de Diego, cuesta LLM): `4_enrich_events_extract.py`.** El guard es `p.eventExtracted IS NULL OR p.eventExtracted = false`, y `eventExtracted = true` se escribe sobre **todo** post procesado, incluidos los descartados en Capa 1 y Capa 2a (`processed_ids` se llena también con los rechazados). El pipeline de eventos fue **reescrito en 2026-08** (Capa 2b eliminada, tipificación movida al LLM, gating de creación, y sobre todo la hipótesis NLI en el idioma detectado del caption — el propio CLAUDE.md dice que la hipótesis fija en español "hundía los scores de captions en inglés/francés independientemente del contenido"). Consecuencia: todos los posts EN/FR procesados por la versión vieja quedaron marcados `eventExtracted=true` y **nunca se reevaluarán** bajo la lógica nueva — mismo bug que `geocodeConfidence`, pero sobre el corpus de posts. Nadie escribe `eventExtracted=false` en ningún lado, así que esa rama del `OR` es código muerto. Opciones para Diego: (a) un centinela de versión (`p.eventExtractVersion`) con el guard mirando también ese campo — análogo exacto al fix de geo; (b) un backfill acotado a los posts cuyo caption no es español; (c) no hacer nada. **No se implementó ninguna: reprocesar dispara llamadas a Groq/Cerebras y eso no se gasta sin permiso explícito.**
     - **`backfill_events_capa3.py` — approach correcto en espíritu, pero con un bug propio, NO tocado.** Es el único script que ya venía "consciente" del problema (guard `e.sourceAuthor IS NULL OR e.locationCapa3 IS NULL`, es decir un segundo campo-centinela para la lógica nueva: exactamente el patrón que se recomienda). El bug: cuando el LLM falla en ambos proveedores, el `SET` escribe `locationCapa3 = false` (`not _llm_call_failed(...)`), pero el guard pregunta `IS NULL` — un `false` no es `NULL`, así que el evento **no vuelve a la cola**, contradiciendo su propio docstring ("uno que falló por rate-limit/proveedores caídos se queda pendiente para reintentar"). `sourceAuthor` tampoco lo rescata: se escribe igual (`author or ""`) aunque la llamada haya fallado. Fix obvio: `OR e.locationCapa3 = false` en el `WHERE`. **No aplicado**, por dos razones: aumenta gasto de LLM, y hay un segundo problema que hay que resolver primero — `new_score = eventScore * llm_penalty` lee el score **ya guardado**, así que reintentar un evento le aplica la penalización **otra vez** (0.5 → 0.25 → …). El reintento no es idempotente; habría que guardar el score pre-penalización o el penalty aplicado antes de habilitar reintentos.
     - **Sin problema (revisados y descartados):** `2_build_graph.py` (todo `SET` incondicional, solo los timestamps son `ON CREATE`), `3_analyze_network.py` (recalcula todas las métricas desde cero en cada corrida), `load_manual_account_categorization.py` (reescribe la planilla entera), `5_export_dashboard_data.py` (solo lee), `4_enrich_events_resolve.py` (compara todos los pares en cada corrida), `cleanup_legacy_accounts.py`, `seal_legacy_batch.py`, `1_harvest_ig_profiles.py`, `1_harvest_ig_posts.py` (su ventana incremental sale del JSON local, no de un campo congelado en el grafo).
     - **Menores, reportados sin tocar:** (i) `extract_profiles.py` tiene un bug de precedencia en Cypher — `WHERE a.followersCount IS NULL OR a.followersCount = 0 AND a.username IS NOT NULL` se evalúa como `IS NULL OR (= 0 AND username IS NOT NULL)`, así que una cuenta con `username` nulo y sin `followersCount` entra igual a la cola de scrapeo (gasto de Apify + `data_raw/profile_None.json`); se arregla con paréntesis. (ii) `exclude_accounts.py` tagea pero nunca destagea: sacar un username de `config/excluded_accounts.json` deja el `outOfScope=true` viejo pegado en el grafo. (iii) `4_enrich_events_resolve.py` excluye en silencio los `:Event` sin `embedding` — nunca se deduplican y nada los repara.
   - Con esto, el punchlist de geo del DD-045 original queda resuelto en su forma actual (fixes reactivos en el export). La causa raíz (fallback ciego en `4_enrich_locations.py`, identidad de `:Location` por string exacto sin normalizar) sigue sin tocarse — la propuesta de arreglarla de raíz con contexto de cuenta + arbitraje LLM sobre candidatos reales de Nominatim quedó discutida pero no implementada, pendiente de agendar.
   - **Bug nuevo encontrado corriendo el `--dry-run` real del backfill (2026-08-17): orden de tiers invertía la prioridad del hint.** `geocode_location()` probaba primero `name_only` (el nombre solo, sin ninguna restricción geográfica) y solo si eso fallaba intentaba `city_combined` (nombre + hint). Como Nominatim resuelve casi cualquier string contra ALGÚN lugar del mundo sin hint, el tier "solo nombre" casi siempre "tenía éxito" primero y el hint por-evento (fix 1 de este mismo punto, 210/780 casos con hint propio) nunca llegaba a probarse. Evidencia del dry-run real: 0 de ~500+ resultados exitosos usaron `city_combined` — el mecanismo de hint estaba, en la práctica, inerte. Casos concretos observados: "Consulado" (hint="Accra") → Ciudad de México; "Cartier" (hint="París") → Nueva York; "DE LA" → Bari, Italia; "Embajada Argentina" → Beijing; "IHEAL" (instituto real de París) → Reino Unido; "Sala I, UNESCO" (hint="Paris, France") → Seúl. Conteo visual (no exhaustivo) sobre el dry-run: ~10-15% de los "✅ encontrado" visiblemente implausibles, más un número indeterminado de casos "silenciosamente mal" (ciudad real pero equivocada, no salta a la vista sin verdad de terreno). **Fix aplicado:** se invirtió el orden — `city_combined` primero, `name_only` (renombrado desde `exact`, que era un nombre engañoso: ese tier nunca fue "exacto", es el menos restringido de los dos) como último recurso. No elimina el ruido de nombres genéricos sin ningún candidato plausible ni con hint (ésos van a seguir cayendo mal), pero corrige el caso mayoritario donde el hint sí tenía la respuesta correcta y no se usaba. Validado con `py_compile`; pendiente que Diego corra el `--dry-run` de nuevo para comparar antes/después.
   - **Pregunta de Diego sobre si conviene mejorar el LLM en vez de/además de esto:** revisando el prompt de Capa 3 (`4_enrich_events_extract.py`, `_llm_schema_hint`/`_build_llm_prompt`), la instrucción actual para `exact_address` era "SOLO si aparece textualmente en el caption" — exigía presencia literal en el texto, pero no exigía que ese texto fuera semánticamente un lugar. Por eso el LLM copiaba fielmente fragmentos como "Consulado", "DE LA", "Sur", "Cartier" cuando aparecían en el caption, aunque no fueran nombres de venue/dirección reales — cumplían la regla tal como estaba escrita.
   - **Implementado (2026-08-17), solo hacia adelante — Diego confirmó explícitamente que NO se reprocesa nada existente, solo aplica al próximo scrapeo, así que no hay gasto de LLM sobre datos ya extraídos.** Se agregó una restricción semántica explícita en dos lugares del prompt (`_llm_schema_hint` — comentario de `exact_address` en el esquema — y el cuerpo de `_build_llm_prompt`): `exact_address` solo se llena si el texto nombra un lugar físico concreto (dirección, edificio, plaza, parque, institución con nombre propio) y NUNCA si es una palabra genérica suelta (ejemplos dados en el propio prompt: "consulado", "remesas", "sur"), un verbo, un nombre de persona, una marca sin dirección, o el título de una campaña/evento — con la prueba explícita "si alguien viera ese texto solo, sin nada más de contexto, ¿alcanzaría para ubicarlo en un mapa?". Se reforzó también la instrucción ya existente de preferir `null` a inventar: ahora dice explícitamente "es preferible decir que no se encontró ubicación a inventar o adivinar una" — mismo criterio que ya regía para `city`, ahora explicitado también para `exact_address` con lenguaje de "no encontrado" en vez de solo "null". Validado con `py_compile`; no se hizo un test end-to-end contra un LLM real (no hay llamada de prueba sin gastar cuota) — el efecto real se confirma con el próximo scrapeo, no antes.

4. ✅ **Hecho (2026-08-15):** los contadores de las pills de categoría/zona/"Todo" en `renderFilterBar()` (`site/app.js`) se calculan sobre `DATA.events` completo (todos los eventos, pasados incluidos), no sobre lo que realmente pasa el filtro de fecha activo (`STATE.when`). Por eso una categoría puede mostrar "Cine: 6" aunque los 6 eventos de cine ya hayan pasado y no aparezcan en ningún lado de la vista por defecto ("Próximos"). **Implementado:** `renderFilterBar()` ahora calcula `zoneCounts`/`themeCounts`/el conteo de "Todo" sobre `upcomingEvents` (helper nuevo `isUpcoming()`, misma regla que ya usaba la rama "upcoming" de `applyFilters`), no sobre `DATA.events` crudo. Se agregó una 5ª pill de fecha, "Pasados" (`STATE.when = "past"`), con su propio conteo — `applyFilters()` la trata como bucket separado que ignora el filtro de tema a propósito (sin clasificar, como pidió Diego) y `render()` la muestra como grilla plana, sin héroe ni destacados. Validado con Node puro (sin DOM, mismo patrón que DD-044 por el cuelgue de jsdom en esta carpeta sincronizada): `isUpcoming`, filtro por fecha en "upcoming" y en "past", y que el tema se ignore en "past" — los 4 casos centrales pasaron; un 5º caso de control dio falso negativo por un stub incompleto en el harness de prueba (`CATEGORY_META` vacío), no por un bug real — confirmado leyendo `eventThemes()`.
   - **Corrección de alcance (2026-08-15, decisión de producto de Diego — reemplaza el criterio de 2026-08-13):** los tres fixes anteriores eran correctos como *detección*, pero equivocados como *consecuencia*: estaban conflando dos cosas independientes. **(a) Que un evento merezca aparecer en el sitio** depende solo de que el LLM haya extraído algún texto de ubicación (`exactAddress` / `locationName` / `cityName`, campos del `:Event`, no del geocoder) — que es exactamente lo que el front renderiza (`ev.exactAddress || ev.locationName || ev.cityName`, `site/app.js`), y el visitante puede verificar la ubicación real con el link "Ver publicación original" que ya existe. **(b) Que un evento tenga pin en el mapa** (feature todavía no construida, punto 5 de esta lista) sí depende de que la geocodificación sea confiable. Con el criterio viejo, un fallo de Nominatim borraba el evento del sitio: 170 → 71 eventos, casi 100 eventos culturales reales invisibles por un problema del geocoder, no del evento. Cambio implementado en `5_export_dashboard_data.py`: (i) `EVENTS_QUERY` ya no exige `l.lat`/`l.lon` — el `OPTIONAL MATCH` a `:Location` quedó realmente opcional (lat/lon salen `null`) y el nuevo requisito duro es `trim(coalesce(...)) <> ''` sobre al menos uno de los tres campos de texto de ubicación; (ii) `_filter_fallback_coordinates()` y `_dedupe_conflicting_locations()` ya no descartan el evento: le setean `lat = None` / `lon = None` y lo dejan en la exportación, con prints reformulados ("se mantienen, sin pin de mapa"); (iii) `_dedupe_conflicting_locations()` sigue colapsando a una sola fila por `id`, pero ahora conserva la fila (sin coordenada) en vez de tirarla. Verificado que `compute_ranking_subscores()` (Q/A/B/penalty: eventScore, confidence, métricas de grafo de la cuenta, hotness, postCount, priceRange) y `compute_similar_events()` (solo embeddings) no leen `lat`/`lon` en ningún punto, y que `site/app.js`/`index.html`/`style.css` tampoco los referencian (grep: cero ocurrencias de `ev.lat`/`ev.lon` — el sitio nunca mostró nada derivado de Nominatim), así que nada rompe con eventos sin coordenada. Validado con `py_compile` y un test aislado con datos sintéticos en memoria (sin Neo4j): coordenada promiscua → `lat/lon = None` con el evento presente en la salida; venue real repetido → conserva su coordenada; filas en conflicto para un mismo `id` → una sola fila sin coordenada; duplicado cartesiano idéntico → colapsa conservando la coordenada; ranking y similares corren sin error con eventos sin geo. **Nota para cuando se construya el mapa (punto 5):** los eventos con `lat`/`lon` en `null` deben quedar fuera del mapa o marcarse explícitamente como ubicación tentativa — no inventarles un pin.
3. Revisar/retirar del scrape las cuentas de Alianza Francesa físicamente en Colombia (y auditar si hay otras cuentas fuera de área coladas igual)
4. Mejorar precisión de fechas — enfoque aún por definir; Diego descartó que la solución pase necesariamente por el LLM ("actualmente es muy mediocre")
5. ✅ **Implementado (2026-08-17) — mapa con pines, no zonas clickeables.** Antes de construir, se le mostraron a Diego mockups visuales de dos enfoques (mapa real con pines vs. arrondissements/comunas como formas clickeables que filtran la lista) — eligió pines, con el basemap lo más personalizado posible hacia la estética del sitio. Implementado con Leaflet 1.9.4 (CDN cdnjs) + tiles CartoDB Positron (`{s}.basemaps.cartocdn.com/light_all`, gratis, sin API key, atribución OSM+CARTO conservada por ToS). El tinte cálido hacia la paleta del sitio es un filtro CSS sobre `.leaflet-tile-pane` únicamente (`grayscale/sepia/saturate/hue-rotate/contrast`, ver `style.css`) — primera pasada de valores, no verificada visualmente por mí (sin navegador en este entorno), pendiente de que Diego la vea desplegada y ajuste si hace falta. Pines: `L.divIcon` coloreado con el mismo `CATEGORY_META` que ya usan las tarjetas (no colores nuevos). Toggle "Ver mapa"/"Ver lista" nuevo en el filterbar (pill, junto a "Gratis"); el mapa respeta los filtros activos (geo/cuándo/tema/gratis) vía `applyFilters()`, y dentro de eso solo pinta los eventos con `lat`/`lon` no nulos — con la decoupling de DD-045 punto 3, eso es hoy 284/596 (47.7%), el resto sigue viéndose solo en la lista. Mapa Leaflet se crea una sola vez (`ensureMap()`) y se reusa entre toggles, con `invalidateSize()` al mostrarse (Leaflet no calcula bien su tamaño si nace con el contenedor en `display:none`). Clic en un pin abre el mismo `detail-overlay` que las tarjetas — no hay una vista de mapa separada del resto de la UI. Traducciones ES/FR agregadas (`viewMap`/`viewList`/`mapCaption`/`mapEmptyTitle`/`mapEmptyBody`). Validado con `node --check` (sintaxis) y verificación cruzada de que todos los `id` referenciados en `app.js` existen en `index.html` — sin verificación visual real (no hay navegador en este entorno), eso queda pendiente de que Diego lo vea desplegado.
   - **Fuera de alcance de esta entrega, documentado a propósito:** la opción de zonas clickeables (arrondissements/petite couronne como filtro, cubriendo también eventos sin pin) se discutió pero no se implementó — queda como posible entrega futura si el mapa de pines resulta insuficiente.
6. Banco de ~50 imágenes genéricas por categoría (fotografía, teatro, etc.) — candidato a trabajo con Opus/design
7. 🔶 **En progreso (2026-08-19) — ver DD-046.** Expansión mucho más allá del pool `discoveredViaCuratedAccount`/`candidateReviewStatus` original: se sumó el trabajo de una sesión aparte (HikerAPI + scraper manual de consola) y los `relatedProfiles` que Apify ya trae gratis en cada `profile_*.json`.
8. Traducción ES/FR del contenido de eventos — al final de la lista a propósito, como edición a la pipeline de extracción (nuevo `titleFr`/`descriptionFr` generado por el LLM en `4_enrich_events_extract.py` al crear el evento); aplica solo a eventos nuevos, no retroactivo sobre los 170 existentes

## DD-046 — Selección de cuentas nuevas: unificación de fuentes + clasificador por embeddings (piloto)

**Fecha:** 2026-08-19
**Contexto:** Diego trajo un handoff completo de otra sesión de chat que trabajó la expansión de red (fase de extracción, previa a `2_build_graph.py`) por dos vías: HikerAPI automatizado (9 semillas, 2196 cuentas únicas en `data_processed/candidate_accounts.csv`) y un scraper manual de consola sobre los modales "siguiendo" de Instagram, procesado con el nuevo `manual_scrape_ingest.py` (6 semillas, 4071 cuentas únicas en `data_processed/manual_candidate_accounts.csv`). Ninguna de las dos fuentes trae biografía ni `businessCategoryName` — solo username/verificado/privado — así que no se puede clasificar nada todavía sin un paso de enriquecimiento aparte.

**Hallazgo nuevo de esta sesión:** los `relatedProfiles` que Apify ya devuelve gratis en cada `profile_<username>.json` (campo que `2_build_graph.py` ya usa para crear el pool `discoveredViaCuratedAccount`/`candidateReviewStatus='pending'`, ver `cleanup_legacy_accounts.py`) NO se habían agregado a este ejercicio de selección. Contados sobre los 276 `profile_*.json` existentes: 65 perfiles traen la lista (promedio ~35 cada uno, no ~25 como estimaba Diego de memoria), **1974 cuentas relacionadas únicas**, de las cuales **1855 nuevas** (no estaban en los 6042 de las otras dos fuentes). Unión total combinando señal de las tres fuentes (seed de following + related-profile-de-cuenta-curada): **7897 cuentas candidatas únicas** — ninguna con bio todavía.

**Criterio de selección (Diego, parafraseado):** cuentas comunitarias — no excluir centros de eventos culturales; sí excluir fiestas/vida nocturna; no excluir nada científico (física, geología, arqueología); incluir literatura y teatro; categoría nueva — "terceros lugares" (concepto de Ray Oldenburg, espacios de encuentro social donde se puede conocer gente: cursos, charlas, mesas redondas); excluir personas individuales.

**Decisión de arquitectura — embeddings antes que LLM, y solo si hace falta:** Diego preguntó si convenía usar Haiku o un modelo más liviano para clasificar. Se decidió no gastar tokens de LLM para el grueso del trabajo: `classify_candidate_accounts.py` (nuevo) reusa el mismo modelo que ya usa el proyecto para eventos (`paraphrase-multilingual-MiniLM-L12-v2`, sentence-transformers, corre local, cero costo por llamada) — Capa 1 compara la bio+nombre+categoría de cada cuenta contra dos bancos de frases ancla (`POSITIVE_ANCHORS`/`NEGATIVE_ANCHORS`, multilingües ES/FR/EN) por similitud coseno máxima; Capa 2 son reglas duras gratis solo para lo inequívoco por keyword (fiesta/discoteca/vida nocturna — `NIGHTLIFE_KEYWORDS`). Deliberadamente NO hay regla dura para "persona individual": el `businessCategoryName` de Instagram no lo distingue de forma confiable, queda en manos del embedding (ancla negativa) a afinar con el piloto. No hay Capa 3 (LLM) todavía — la idea es medir con el piloto de 50 si hace falta antes de gastar ahí.

**Salida:** un solo CSV ordenado por `score` descendente (`pos_score - neg_score`) con columna `bucket` (fijo/posible/descartar, por umbral — `THRESHOLD_FIJO=0.15`/`THRESHOLD_POSIBLE=0.0`, primera pasada sin calibrar) en vez de dos documentos separados, para que Diego filtre/ordene él mismo — mismo espíritu que ya usa en `cuentas_instagram_completo_v4.xlsx` (126 cuentas ya curadas a mano por Diego, con `Tipo de institución`/`Tipo de arte`/etc. — quedan como banco de verdad conocida para calibrar los umbrales cuando se corra el piloto contra ellas).

**Piloto (arranca chico, a pedido de Diego):** en vez de gastar en las 7897 de una, se armó `config/seeds_pilot_account_classification.json` con las **50 cuentas de mayor señal combinada** (ranking por cantidad de fuentes distintas que la surfacearon — semilla de following O related-profile-de-cuenta-curada — no solo un tipo de señal). Ejemplos del top: instituciones culturales grandes esperables (`franceinter`, `centrepompidou`, `museelouvre`, `telerama`) mezcladas con cuentas gubernamentales/consulares (`consuladocolparis`, `ubpdcolombia`, `minjusticiaco`) que deberían caer del lado "descartar" con el criterio de Diego — buen caso de prueba real para calibrar el umbral.

**Próximo paso (para Diego, en su venv — `sentence-transformers` no está disponible en el sandbox de este agente):**
```
python 1_harvest_ig_profiles.py --seeds config/seeds_pilot_account_classification.json
python classify_candidate_accounts.py --usernames-file config/seeds_pilot_account_classification.json --out data_processed/pilot_classification.csv
```
Costo estimado del scrape de 50 perfiles: trivial (~$0.03-0.20 según el precio histórico calibrado en `.apify_cost_log.json`, catálogo base $0.0005/perfil). El clasificador en sí no tiene costo — corre local.

**Validado sin `sentence-transformers` (no disponible en este sandbox):** `py_compile`, y pruebas unitarias en aislado de toda la lógica pura (`hard_exclude_reason`, `candidate_text`, `load_usernames`, `load_profile` contra un `profile_*.json` real del repo — `104paris`, categorizado correctamente como "Cultural Center"). La parte de embeddings (similitud coseno, umbrales) NO se corrió todavía — eso depende del piloto que corra Diego.

**Pendiente / decisiones abiertas:**
- Calibrar `THRESHOLD_FIJO`/`THRESHOLD_POSIBLE` con los resultados reales del piloto (y opcionalmente contra las 126 cuentas ya curadas en `cuentas_instagram_completo_v4.xlsx` como banco de verdad).
- Decidir, después del piloto, si hace falta una Capa 3 (LLM barato — Haiku o el mismo Groq/Cerebras gratuito que ya usa el proyecto) para los casos "posible"/ambiguos, o si los embeddings solos alcanzan.
- Unificar de verdad `candidate_accounts.csv` + `manual_candidate_accounts.csv` + los `relatedProfiles` en un solo CSV de candidatas (hoy siguen siendo fuentes separadas que solo se combinaron en memoria para rankear el piloto, no hay un archivo consolidado en disco todavía).
- Decidir el tamaño de la siguiente tanda después de ver el piloto (¿los 1855 related-profiles nuevos? ¿los ~429 de 2+ semillas? ¿todo?) — depende de qué tan bien funcionen los embeddings en los 50.

**Corrección de rumbo (2026-08-19, mismo día): Diego no confía en el enfoque de embeddings y pide criterio directo de LLM, sin scraping nuevo.** Se descarta `classify_candidate_accounts.py` (embeddings) como método principal — mismo aprendizaje que el proyecto ya tuvo con los eventos (Capa 2b por embeddings se retiró porque "el LLM clasifica mejor y sin sesgo de las referencias", ver docstring de `4_enrich_events_extract.py`). Nuevo método, sin script nuevo que mantener: subagentes Haiku (vía el tool de Agent, `model=haiku`, `subagent_type=general-purpose` para que tengan búsqueda web) reciben el criterio completo en el prompt y juzgan directo — score 0-100, bucket (fijo≥75/posible 40-74/descartar<40), origen de la evidencia (bio/búsqueda/sin_info) y una cita corta. Explícitamente: **no se scrapea nada nuevo vía Apify** — para las cuentas sin bio en disco, el propio subagente busca en la web en el mismo paso.

**Piloto ejecutado (2026-08-19):** las 50 cuentas de `config/seeds_pilot_account_classification.json`, repartidas en 5 lotes de 10, cada uno un subagente Haiku independiente. Resultado en `data_processed/pilot_classification.csv`: 15 fijo, 9 posible, 24 descartar, 2 sin_info. Consistencia notable con decisiones ya tomadas antes en el proyecto sin que se le dijera explícitamente al subagente: `francia_en_colombia` (embajada francesa en Bogotá) descartada por estar fuera de la región de París — el mismo criterio geográfico de DD-045 (exclusión de las Alianzas Francesas en Colombia), a que el subagente llegó de forma independiente. `themuseumofmodernart`/`metmuseum` descartados correctamente por estar en Nueva York, no en Francia.
   - **Limitación real observada:** el lote 1 (10/50 cuentas) reportó explícitamente "No pude acceder a las páginas de Instagram ni a resultados de búsqueda útiles" y respondió "basándome en conocimiento general" en vez de con búsqueda real — riesgo de alucinación en cuentas menos conocidas de ese lote específico (`franceinter`, `telerama`, `artefr`, `cultura.hay`). Los otros 4 lotes sí citaron fuentes reales de búsqueda (URLs de Instagram/Wikipedia). No se investigó la causa raíz (¿el tool de búsqueda no cargó a tiempo en ese subagente en particular?) — a tener en cuenta si se repite en la próxima tanda.
   - Editoriales (Gallimard, Grasset, Zulma, etc.) cayeron mayormente en "posible"/"descartar" por ser promoción comercial de libros sin evidencia de eventos/club de lectura activo — tensiona con el pedido de Diego de "no excluir... promoción de literatura"; a revisar juntos si el umbral está bien calibrado para ese caso o si la instrucción necesita un matiz (¿toda editorial entra, o solo las que organizan eventos?).
   - Costo: ~143.000 tokens de Haiku en total repartidos en los 5 subagentes (barato comparado con hacerlo en la conversación principal). Reporte de tokens/porcentaje de límite pedido por Diego vía la skill `explain-usage`.

**Corrección de rumbo 2 — recalibración anti-institucional (2026-08-20).** Diego revisó el CSV del piloto y dio feedback: el criterio general está bien, pero el método puntúa demasiado alto instituciones grandes/oficiales con programación fácil de encontrar (museos nacionales, teatros nacionales — ej. Louvre 94, Grand Palais 95, Théâtre de l'Odéon 95) y demasiado bajo librerías/editoriales pequeñas y cuentas que difunden eventos sin organizarlos ellas mismas. Pidió una segunda tanda de 50 con el mismo método, agregada al mismo CSV, con conteo final.

- **Selección de la tanda 2:** siguiente bloque de 50 por el mismo ranking de señal combinada usado en la tanda 1 (cantidad de fuentes distintas — seed de following + related-profile — que surfacearon cada cuenta), continuando en el puesto 51 del pool de 7897 sin repetir las 50 ya clasificadas. Guardado en `config/seeds_pilot_account_classification_round2.json`. Nota: este método de selección por popularidad de señal es en sí mismo parte de por qué la tanda 1 salió sesgada hacia lo institucional (las cuentas grandes son las que más seeds/cuentas curadas las mencionan) — queda pendiente de discutir con Diego si conviene cambiar el método de selección de la tanda 3, no solo el de puntaje.
- **Instrucción nueva a los subagentes Haiku:** ser grande/oficial/famoso ya no suma puntos por sí solo. Suman: librerías independientes pequeñas, editoriales/revistas culturales pequeñas, espacios comunitarios de barrio, y cuentas que difunden/curan eventos de terceros sin organizarlos — eso último ahora cuenta a favor explícitamente, no en contra. El resto del criterio (excluir fiesta/individuos, incluir ciencia/literatura/teatro/terceros lugares) no cambió.
- **Resultado tanda 2:** ejemplos de la recalibración funcionando — `parismusees` (red de 14 museos) bajó a 38/descartar, `theatrechaillot`/`theatrechatelet` (teatros nacionales) bajaron a posible en vez de fijo automático, mientras que `librairiemillepages` (librería independiente en Vincennes) 85/fijo, `labellevilloise` (espacio artístico independiente) 85/fijo, `blogotheque` (curador independiente de música que no organiza los shows) 85/fijo, y `atlf_traduction` (asociación pequeña de traductores literarios) 78/fijo puntuaron alto. Igual que en la tanda 1, varias cuentas colombianas de gobierno/cooperación (`kas_colombia`, `defensoriacol`, `ideaspaz`, `koica_colombia`, etc.) se descartaron por no tener programación cultural propia — el subagente aplicó bien el matiz pedido explícitamente en el prompt de este lote.
- **Total acumulado tras las 2 tandas (100 cuentas):** 30 fijo, 21 posible, 46 descartar, 3 sin_info.
- **Limitación técnica encontrada:** no se pudo escribir directamente sobre `data_processed/pilot_classification.csv` (falló tanto por bash como por el editor de archivos con error de permiso/rename — probablemente el archivo estaba abierto en otro programa, ej. Excel, del lado de Diego). Se creó `data_processed/pilot_classification_v2.csv` con las 100 filas (50+50) como archivo nuevo en vez de editar el original in place. **Pendiente:** Diego debe cerrar el archivo original si lo tiene abierto, o confirmar si quiere que `pilot_classification_v2.csv` reemplace al original de ahora en más.

**Consolidación + tanda 3 (2026-08-20, mismo día).** Diego confirmó que el archivo original ya estaba libre (lo tenía abierto en Excel) y pidió: (a) unificar en un solo CSV, y (b) correr 300 cuentas más con el mismo método. Se sobrescribió `data_processed/pilot_classification.csv` con las 100 filas existentes y se borró `pilot_classification_v2.csv` (vía `allow_cowork_file_delete`, ya no hace falta mantenerlo).

- **Selección de la tanda 3:** siguiente bloque de 300 por el mismo ranking de señal combinada, continuando después de las 100 ya clasificadas (posiciones 101-400 del pool de 7897). Guardado en `config/seeds_pilot_account_classification_round3.json`.
- **Ejecución:** 30 subagentes Haiku de 10 cuentas cada uno, mismo prompt recalibrado que la tanda 2 (confirmado por Diego). **Limitación real encontrada — presupuesto de WebSearch de la sesión agotado a mitad de camino:** el límite de búsquedas web de esta sesión de Cowork (`CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`, compartido entre todos los subagentes de la sesión, no por subagente) se agotó tras ~20 de los 30 lotes. Resultado real:
  - **19 lotes (190 cuentas) con búsqueda web real** — resultado confiable, igual que las tandas 1 y 2.
  - **1 lote (10 cuentas, `bingeaudio`...`citeinternationaleparis`) parcial:** 4 con búsqueda real, 6 marcadas `sin_info`/score 0 porque la búsqueda falló a mitad del lote (presupuesto agotado durante su ejecución).
  - **1 lote (10 cuentas, `librairiegallimard`...`marienvrac`) sin ninguna búsqueda real:** el subagente devolvió estimaciones basadas solo en conocimiento previo, marcadas explícitamente `origen=sin_info` — **no verificado, tratar con más escepticismo que el resto del CSV.**
  - **9 lotes (90 cuentas) sin ningún dato:** el subagente se negó a clasificar sin poder buscar y devolvió un mensaje pidiendo que se suba el límite, en vez de adivinar. No hay fila en el CSV para estas 90 — quedan pendientes, no descartadas. Lista completa en `/tmp/r3/missing_round3.json` de esta sesión (no persistido en el repo — hay que regenerarla si se retoma: son las cuentas de `config/seeds_pilot_account_classification_round3.json` que no aparecen en el CSV final).
- **Total clasificado en la tanda 3:** 210 de las 300 seleccionadas (200 con evidencia real + 10 no verificadas). **Total acumulado en las 3 tandas: 310 cuentas clasificadas** (de las 400 que se intentaron seleccionar) — 82 fijo, 85 posible, 117 descartar, 26 sin_info.
- **Pendiente:** completar las 90 cuentas que no llegaron a clasificarse, en una sesión nueva (el límite de búsquedas parece resetear por sesión, no hay forma de subirlo desde acá) o pidiendo explícitamente subir `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`. También pendiente: decidir si el lote de 10 sin verificar (`librairiegallimard` etc.) se re-corre con búsqueda real antes de confiar en esos scores.

**Lista maestra + reparto de trabajo (2026-08-20, mismo día).** Diego pidió convertir `pilot_classification.csv` en la lista maestra completa: las 7897 cuentas del pool (no solo las clasificadas), agregando una columna `clasificado_por` — vacía para que Diego marque cuenta por cuenta si la clasifica él o si me la deja a mí. Las 310 ya clasificadas quedaron con `clasificado_por=claude`. Plan de Diego: él descarta a mano lo obvio (cuentas de personas individuales, basura visible) para no gastar más tokens en eso, y me deja los casos difíciles a mí. El archivo ahora tiene 7897 filas × 6 columnas (`username,score,bucket,origen,cita,clasificado_por`), ordenado por la misma señal combinada usada para priorizar las tandas anteriores (cantidad de fuentes distintas que surfacearon cada cuenta) — así las cuentas ya vistas por más semillas/cuentas curadas quedan arriba. Verificado: 7897 filas únicas, 310 con `clasificado_por=claude`, 7587 vacías. **Siguiente paso:** esperar a que Diego marque una porción del CSV y avise qué cuentas me tocan a mí.

**Reparto en marcha (2026-08-20, mismo día).** Diego revisó el CSV en Excel (lo re-guardó con `;` como separador — el CSV ahora usa `;`, no `,`), descartó a mano gran parte de lo obvio, y marcó 560 cuentas con `x` para que yo las evalúe. De esas 560, en esta ronda se clasificaron 180 (466 `clasificado_por=claude` en total, sumando lo de antes) antes de volver a chocar con el límite de `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` compartido de la sesión — igual que en la tanda 3. **Quedan 380 de las 560 todavía marcadas `x` sin tocar**, pendientes de continuar cuando el presupuesto de búsqueda se libere. Un hallazgo lateral de Diego mientras revisaba (documentado también en `CLAUDE.md`, sección "Lessons for replicating this project in another city"): ~90% de las cuentas candidatas caen en un puñado de raíces de palabra recurrentes (théâtre, littérature, librairie, livre, impro, asso...) — para una futura réplica en otra ciudad, buscar por esas palabras clave sería mucho más barato que scrapear el grafo completo de seguidores.

**Continuación en sesión nueva (2026-08-20/21).** Diego confirmó estar en una sesión de Cowork nueva ("ya estamos en otra sesión, continua capitán") — el presupuesto de WebSearch efectivamente estaba fresco: se clasificaron 90 cuentas más (18 lotes, 9 completos con búsqueda real + 1 parcial) antes de volver a topar con el límite de 200 búsquedas, esta vez confirmando que el límite es por sesión (no por ventana de tiempo dentro de la misma sesión) — se agotó exactamente después de un volumen similar (~180 cuentas) al de la vez anterior. **Total acumulado marcado `clasificado_por=claude`: 556 de 7897. Quedan 290 de las 560 `x` sin tocar**, para la próxima sesión nueva.

**Tanda 4 — cierre parcial de las 560 `x` (2026-08-21, sesión nueva tras el feature de staging).** Se dispararon los 29 lotes restantes (290 cuentas) en paralelo, mismo prompt recalibrado. Resultado: **182 cuentas clasificadas con datos reales o `sin_info` explícito** (738 `clasificado_por=claude` acumulado), **108 sin ningún dato** — quedan marcadas `x`. Dos causas distintas de las cuentas sin dato, no la misma de siempre:
- La mayoría de los 108 son lotes que agotaron el presupuesto de `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` (200/sesión, compartido entre subagentes) — mismo patrón que las tandas 3 y 3.5.
- **Hallazgo nuevo, más serio:** al menos 2 subagentes terminaron con `"Agent terminated early due to an API error: You've hit your monthly spend limit"` — esto es un tope de gasto mensual a nivel de cuenta de Anthropic, no el presupuesto de búsquedas por sesión. A diferencia del límite de WebSearch (que se refresca con una sesión nueva), este tope **no se resuelve esperando una sesión nueva** — requiere que Diego lo suba en `claude.ai/settings/usage`. No se investigó más a fondo si esto afectó también a otros lotes que fallaron silenciosamente sin mensaje explícito.
- **Bug de parseo encontrado y corregido en el propio proceso de merge (no en el pipeline productivo):** algunos subagentes devolvieron citas con `;` interno (ej. "Eventos autor signings; editorial corporativa"), lo que rompía un primer intento de parseo por `split(';')` sin límite — 5 cuentas con datos reales casi se perdieron por este bug. Corregido usando `split(';', maxsplit=4)` para que los `;` extra queden dentro del campo `cita`. Vale la pena tenerlo en cuenta si se automatiza este merge en el futuro.
- Lista completa de las 108 cuentas aún sin clasificar disponible en el propio CSV (`clasificado_por=x`) — no se generó un archivo aparte esta vez.
- **Total acumulado: 738 de 7897 clasificadas (280 descartar, 209 fijo, 174 posible, 75 sin_info). Quedan 108 de las 560 `x` originales sin clasificar.**

**Cierre del ejercicio (2026-08-21, mismo día).** Diego decidió clasificar a mano las 108 cuentas restantes en vez de seguir peleando con los límites de sesión/gasto — se le entregó el CSV directamente (ya estaba en su carpeta). Con esto se da por cerrado el reparto de trabajo Claude/Diego sobre las 7897 cuentas: **738 clasificadas por Claude (subagentes Haiku + búsqueda web), 108 pendientes de clasificación manual por Diego, 7051 sin marcar para revisión (ni `x` ni `claude`)** — estas últimas quedan tal cual, a criterio de Diego si las retoma más adelante.

**DD-047 — Staging zone para eventos: `:PendingReview` + `review_events.py` (2026-08-21).** Diego pidió una forma de revisar los eventos antes de que salgan al sitio (aprobar/editar/borrar), preferentemente algo interactivo y de bajo esfuerzo. Dos decisiones cerradas con Diego antes de construir: (1) "borrar" es **siempre** rechazo blando, nunca un `DETACH DELETE` real, para no perder nada por accidente; (2) mockup visual primero, código después (aprobado antes de implementar).

- **Mecanismo elegido:** reutilizar el patrón ya existente de `:Rejected` (usado en `5_export_dashboard_data.py`, `export_events_excel.py`, `testing/plot_eventscore_boxplot.py`) en vez de inventar una property nueva tipo `reviewStatus`. Se agregó una segunda label, `:PendingReview`, que ahora se setea automáticamente en `4_enrich_events_extract.py` (rama de creación de evento nuevo dentro de `upsert_event`) — todo `:Event` nace oculto del sitio hasta que Diego lo revisa a mano. Eventos que ya existían en Neo4j antes de este cambio no tienen la label y siguen en vivo sin verse afectados.
- **Exclusión en las salidas:** `5_export_dashboard_data.py` (`EVENTS_QUERY`) ahora excluye `NOT 'PendingReview' IN labels(e)` además de `Rejected`. `export_events_excel.py` suma un flag nuevo `--include-pending` (paralelo a `--include-rejected` que ya existía), off por defecto — mismo criterio de exclusión que el dashboard.
- **UI:** `review_events.py`, app de Streamlit local (`streamlit run review_events.py`), sin hosting ni autenticación — corre en la máquina de Diego. Por cada evento pendiente muestra título/categoría/fecha/ubicación/precio/descripción/fuente, con tres acciones: Aprobar (quita `:PendingReview`, el evento pasa a estar visible), Editar (formulario inline que reescribe título/descripción/categoría/fecha/ubicación/ciudad/precio directo en el nodo), Rechazar (agrega `:Rejected` y quita `:PendingReview` — esto es el único "borrar" de esta función, nunca elimina el nodo).
- **Por qué Streamlit y no revivir el dashboard de Dash:** `old/dash_common.py` + `5_visualize_dashboard.py` + `pages/*.py` ya existían pero están archivados en `old/` — un dashboard de Dash que se construyó una vez y se abandonó, según la convención del proyecto para esa carpeta. Streamlit no requiere ese andamiaje: un solo script, cero configuración de rutas/páginas, arranca con un comando.
- **Dependencia nueva:** `streamlit>=1.40.0` agregado a `requirements.txt`, sección propia junto a la de export a Excel.
- **Pendiente / no resuelto todavía:** no se corrió `review_events.py` contra la base real todavía (requiere `.env` con credenciales Neo4j, que no están en este sandbox) — falta que Diego lo pruebe en su máquina y confirme que el flujo funciona como en el mockup aprobado. Tampoco se decidió si hace falta paginación/filtro por categoría si el volumen de pendientes crece mucho (hoy no es un problema, la lista de pendientes recién arranca).

---

**DD-048 — Trazabilidad de origen en `:Post` (`sourceProfileEmbed`/`sourceDedicatedScraper`) + filtros de recencia y origen en `4_enrich_events_extract.py` (2026-08-24).** Al revisar los conteos tras el primer harvest completo con las 339 cuentas unificadas (126 viejas + 213 nuevas "fijo"), se encontró una fuente de posts no rastreada: `1_harvest_ig_profiles.py` trae un campo `latestPosts` embebido en cada `profile_<username>.json` (los ~12 posts más recientes del actor `apify/instagram-profile-scraper`), y `2_build_graph.py` siempre los ingestó vía la misma `load_posts()` que procesa `posts_<username>.json` — pero **sin ninguna garantía de ventana de días**, a diferencia del scraper dedicado (`1_harvest_ig_posts.py`, DD-029) que sí verifica localmente `onlyPostsNewerThan`. Cruce contra los 530 perfiles en disco: de 5,269 post-ids embebidos en `latestPosts`, solo 690 también pasaron por el scraper dedicado — los otros 4,579 no tienen ninguna garantía de recencia, y 219 cuentas solo tienen perfil scrapeado (100% de sus posts sin filtro de fecha).

- **Investigación de si el JSON en sí diferencia el origen:** sí. Comparando el mismo post-id presente en ambas fuentes (`data_raw/profile_3027.troismillevingtsept.json` vs `data_raw/posts_3027.troismillevingtsept.json`), el `timestamp` es idéntico en ambas (no sirve como diferenciador), pero el embed de perfil **no trae** `latestComments`, `musicInfo`, `firstComment`, `productType`, `inputUrl`, `originalWidth`/`originalHeight` ni `ownerFullName` — campos que sí trae el actor `apify/instagram-post-scraper`. El propio Apify ya diferencia el origen por la forma del JSON; antes no se estaba guardando esa señal en Neo4j.
- **Fix 1 — trazabilidad en el schema:** `load_posts(tx, username, posts, source)` en `2_build_graph.py` ahora recibe `source="profile_embed"` o `source="dedicated_scraper"` según de dónde vino la lista de posts en `process_account()`, y setea dos flags booleanos acumulativos por post (`p.sourceProfileEmbed`, `p.sourceDedicatedScraper`) — acumulativos porque un mismo post puede llegar por ambas vías a lo largo del tiempo; nunca se pisan a `false` una vez en `true` (vía `coalesce`). Como `load_posts` usa `MERGE` por `id`, re-correr `2_build_graph.py` sobre datos ya ingestados retrocompleta el flag en los nodos existentes sin duplicar nada.
- **Fix 2 — filtro de recencia en `4_enrich_events_extract.py`:** nueva opción `--max-post-age-days` (default 20, comparando los primeros 10 caracteres de `p.timestamp` como string ISO en vez de `datetime(p.timestamp)`, para no reventar la query si algún timestamp viene vacío/malformado).
- **Fix 3 — filtro de origen en `4_enrich_events_extract.py`:** nueva opción `--only-dedicated-scraper/--include-profile-embed`, **default `--only-dedicated-scraper`** (`p.sourceDedicatedScraper = true` obligatorio) — decisión explícita de Diego: el pipeline de extracción de eventos corre por defecto solo sobre posts que sí pasaron por la ventana de días verificada, para no gastar Capa 1/2/3 en posts de origen sin garantía de recencia.
- **Pendiente:** correr `2_build_graph.py` de nuevo para que el backfill de `sourceDedicatedScraper`/`sourceProfileEmbed` quede escrito en los 4,754 posts ya ingestados antes de este cambio (en curso al momento de escribir esto). No se verificó si `latestComments`/`musicInfo`/etc. están *siempre* ausentes en el embed para el 100% de tipos de post (imagen vs. video vs. carrusel) — solo se confirmó en un caso concreto.
- **Bug encontrado al validar `--dry-run` con Google (mismo día):** `LLM_PROVIDER=google` fallaba con `404 NOT_FOUND` en todas las llamadas, activando el fallback automático a Groq (DD-033) en cada request. No era un tema de cuota — el body del error decía explícitamente `"This model models/gemini-2.5-flash-lite is no longer available to new users... use models/gemini-3.5-flash-lite"` (confirmado con la API key real de Diego). Google descontinuó el modelo que se había configurado el 2026-08-21. Fix: `GOOGLE_MODEL = "gemini-3.5-flash-lite"`. No se re-verificaron los límites de RPM/TPM/RPD de 3.5-flash-lite contra el dashboard de Google — los valores en `GOOGLE_MAX_RPM`/`GOOGLE_MAX_TPM` siguen siendo los aproximados heredados de 2.5, pendiente de confirmar si difieren.
- **Bug preexistente encontrado al validar el `--dry-run` con DeepSeek:** Neo4j devolvía un warning `property key does not exist: hashtags` en la query de candidatos de `run_extraction()`. La causa: `p.hashtags` nunca fue una propiedad del nodo `:Post` — los hashtags se modelan como nodos `:Hashtag` separados vía `:HAS_HASHTAG` (ver `load_posts()` en `2_build_graph.py`), no como lista embebida. La query pedía `p.hashtags AS hashtags`, siempre `null`, lo que a su vez rompía silenciosamente el loop en `upsert_event()` (línea ~1479) que le pone hashtags a los `:Event` — **ningún evento creado hasta ahora tiene hashtags asociados por este bug**, no relacionado con los cambios de esta misma sesión. Fix: `[(p)-[:HAS_HASHTAG]->(h:Hashtag) | h.name] AS hashtags` en vez de `p.hashtags`. No se hizo backfill de los eventos ya existentes sin hashtags — solo aplica hacia adelante.

---

**DD-049 — Exploración de alternativas más baratas a Apify para descubrimiento de cuentas (2026-08-24).** Motivado por el problema ya documentado en `CLAUDE.md` ("Discovery-source keyword pattern"): el crawl de follower/following completo vía Apify es caro (~$29+ para 126 semillas × 300 cuentas cada una). Diego probó dos alternativas en paralelo para esta fase de descubrimiento (no tocan el pipeline numerado de perfiles/posts/eventos — producen listas crudas de candidatos que necesitarían la misma curaduría LLM/manual que ya se hizo para llegar al pool de 7897 y luego a `pilot_classification.csv`):

- **`1_harvest_ig_network_hikerapi.py` — HikerAPI (pay-per-use, sin mensualidad).** Calibrado en real contra `@sorbonne_lettres_culture` (2026-08-17): auth por header `x-access-key`, resolver username→user_id vía `/v1/user/by/username`, following vía `/v1/user/following/chunk` (paginado, 25 cuentas/página, la forma real de la respuesta es una lista `[usuarios, next_max_id]`, no el objeto `{"users":..., "next_max_id":...}` que dice la doc pública — hallazgo de calibración, no algo documentado por HikerAPI). Costo real confirmado: `$0.0006/request`, ~20 cuentas por request → proyección de ~$1.13 para las 126 semillas completas (~25x más barato que Apify). Ya corrido sobre 9 semillas reales: 90 requests, $0.054, `data_processed/candidate_accounts.csv` con 2,197 candidatos únicos (`seen_by_count` = en cuántas semillas distintas aparece, misma señal usada para priorizar tandas de clasificación en el ejercicio de las 7897). Limitación: no trae biografía ni categoría de negocio — solo pk/username/full_name/is_private/is_verified/account_badges — un enriquecimiento aparte haría falta antes de clasificar con el mismo criterio que se usó para las 7897.
- **`manual_scrape_ingest.py` — scraping manual gratis vía consola del navegador.** Diego copia el DOM del modal de "seguidores"/"siguiendo" de Instagram a mano y lo pega en el script (modo interactivo o `add`); el script separa el badge "Vérifié" del texto, marca como `UNRELIABLE_TEXT:` cualquier fila donde el texto visible no contiene el username (señal de que el DOM-scrape agarró un elemento de navegación de Instagram como "Profil"/"Populaire" en vez del nombre real — no se descarta, solo se marca), dedupea, y guarda en `data_raw/manual_following_<seed>.csv` + un índice (`manual_following_index.json`). Ya corrido sobre 5 semillas reales (`lamagiadelmomentoparis`, `maisondelapoesie`, `opera_comique`, `espacepasolini`, `15x4paris`): 266-793 cuentas por semilla, `data_processed/manual_candidate_accounts.csv` con 4,072 candidatos únicos. Costo cero (sin API), pero esfuerzo manual por semilla — complementario a HikerAPI, no un reemplazo.
- **Limpieza:** se encontró y borró `test_hikerapi_following.py`, un duplicado casi byte-a-byte de `1_harvest_ig_network_hikerapi.py` (mismo código, mismo docstring que ya referenciaba al script "final" por nombre) — quedó suelto de una iteración de desarrollo anterior. `test_10_seeds.txt` y `hikerapi_seeds_batch2.txt` (lotes de semillas de prueba usados como `--usernames-file`) y los cost logs nuevos (`.hikerapi_cost_log.json`, `.apify_network_cost_log.json`, mismo patrón que el ya-ignorado `.apify_cost_log.json`) se agregaron a `.gitignore` — son scratch/telemetría local, no config versionable.
- **Pendiente:** ninguno de los dos métodos está conectado todavía a un paso numerado del pipeline ni a una decisión de cuál usar como principal — quedaron como exploración en curso. Antes de escalar cualquiera de los dos a las 126 semillas completas, falta decidir con Diego si HikerAPI (rápido, pago, sin bio) o el scraping manual (gratis, lento, con bio/contexto visual que Diego puede juzgar al copiar) es el método a usar de ahora en más para descubrimiento, o si se combinan (ej. HikerAPI para volumen, manual para semillas donde HikerAPI falle o para verificación cruzada).

---

**DD-050 — Reordenamiento del fallback de LLM: DeepSeek pasa al final de la cascada (2026-08-24).** Al correr por primera vez `4_enrich_events_extract.py` en real (no `--dry-run`) sobre un lote grande (315 posts, orden `groq→google→deepseek→cerebras` pedido en DD-033 update 8), se observó en vivo que DeepSeek falla con más frecuencia que los otros tres proveedores — pero de una forma distinta a la esperada. No es 429 (cupo agotado, lo normal en Groq/Google/Cerebras cuando su tier gratis se acaba) ni un error de conexión típico: la API de DeepSeek devuelve **HTTP 200 con `message.content` vacío**, lo que revienta el `json.loads()` posterior con el genérico `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` — Diego preguntó primero si esto era un tema de crédito agotado; no lo es, la petición se factura y "tiene éxito" a nivel HTTP, simplemente el motor de inferencia de DeepSeek no devuelve contenido bajo carga, consistente con que su rate-limiting es por concurrencia (no por cupo fijo, ver comentario en `DEEPSEEK_ENDPOINT`) — probable señal de congestión del lado de ellos, no reproducible ni solucionable desde acá.

- **Fix 1 — reordenar la cascada:** `_CLOUD_PROVIDERS` pasó de `groq→google→deepseek→cerebras` a **`groq→google→cerebras→deepseek`**. Razonamiento: DeepSeek es el único de los cuatro que es pago (los otros tres tienen tier gratis con cupo diario); no tiene sentido intentarlo antes que una alternativa gratis que además demostró ser más estable en la misma corrida real (Cerebras no falló ni una vez en la muestra observada). Con este cambio, DeepSeek solo se usa como último recurso cuando los tres gratis fallan/se agotan, no como tercera opción por delante de uno de ellos.
- **Fix 2 — distinguir "content vacío" de "JSON malformado" en `_deepseek_request`:** antes, un `content` vacío caía directo a `json.loads("")`, que revienta con el mismo error genérico que un JSON real pero corrupto — dos causas raíz distintas indistinguibles en el log. Ahora se chequea explícitamente `if not content` y se lanza un `ValueError` con el `finish_reason` que vino en la respuesta (`length`/`content_filter`/etc.), para poder diagnosticar la próxima vez que pase en vez de solo ver "Expecting value" sin contexto.
- **No implementado (evaluado y descartado por ahora):** fallar más rápido en vez de agotar los 3 reintentos por intento cuando el contenido viene vacío — se decidió mantener los reintentos porque, si la causa es congestión transitoria del lado de DeepSeek, reintentar sí puede ayudar (no hay evidencia de que sea un fallo determinístico por prompt).
- Verificado con `py_compile`. Pendiente: correr otra corrida real grande con el nuevo orden para confirmar en la práctica que Cerebras absorbe el fallback sin degradar tiempo total, y decidir si DeepSeek se saca del todo de la cascada default (dejarlo solo accesible con `LLM_PROVIDER=deepseek` explícito) si esta inestabilidad se repite.

---

**DD-051 — Eventos bilingües ES/FR (2026-08-24).** El sitio ya tenía el botón ES/FR (`site/i18n.js`, `CURRENT_LANG`) funcionando para todo el texto de interfaz (menús, filtros, etiquetas), pero el contenido de cada evento (`title`/`description`) se usaba tal cual en `app.js` sin ninguna rama de idioma — 5 sitios distintos (tarjeta, hero, tooltip del mapa, panel de detalle, fila de "similares"). Diego pidió que los eventos también respondan al idioma, con alcance explícito: **solo eventos nuevos, sin backfill de los ya existentes.**

- **LLM (`4_enrich_events_extract.py`):** se agregaron `title_fr`/`description_fr` al mismo JSON que ya le pedimos al LLM por post — misma llamada, sin requests extra, solo más tokens de salida. Instrucción añadida al prompt: son la traducción al francés de `title`/`clean_description`, mismo criterio de longitud, nombres propios sin traducir en ambos idiomas.
- **Neo4j:** nuevas propiedades `titleFr`/`descriptionFr` en `:Event`, seteadas **solo en la rama de creación** de `upsert_event()` (igual que `description`/`sourceAuthor` — nunca se sobreescriben al fusionar con un evento existente, ver comentario ya existente sobre esto en el código). Como no hay backfill, los eventos creados antes de este cambio simplemente no tienen estas dos propiedades.
- **Export (`5_export_dashboard_data.py`):** `EVENTS_QUERY` ahora también trae `e.titleFr`/`e.descriptionFr` — salen `null` para eventos viejos, se exportan igual (el filtrado de idioma pasa en el frontend, no acá).
- **Frontend (`site/app.js`):** dos funciones nuevas, `evTitle(ev)`/`evDescription(ev)`, que devuelven la versión francesa si `CURRENT_LANG === "fr"` **y** el campo existe; si no, caen al español — así un evento viejo sin traducción se sigue viendo (en español) en vez de dejar un hueco vacío al cambiar a modo FR. Reemplazados los 5 usos directos de `ev.title`/`ev.description` (tarjeta, hero, tooltip del mapa, detalle, similares) por estas funciones. Como el botón de idioma ya disparaba `render()` desde antes, el cambio se refleja sin tocar la lógica de filtros/estado.
- Verificado: `py_compile` en los dos scripts Python, `node --check` en `app.js`/`i18n.js`. **Pendiente:** correr una extracción de eventos real para confirmar que el LLM efectivamente devuelve `title_fr`/`description_fr` con buena calidad (no se validó calidad de traducción, solo que el pipe end-to-end no rompe) — y decidir si en algún momento vale la pena hacer backfill de los eventos viejos (fuera de alcance de este pedido puntual).

---

**DD-052 — Filtro por geoZone + rechazo masivo en `review_events.py` (2026-08-25).** Diego reportó, revisando a mano la primera tanda real de eventos pendientes, que estaba rechazando uno por uno absolutamente todo lo etiquetado "Fuera de Francia" — pidió que se pudiera repartir/filtrar por eso en vez de revisar cada uno individualmente.

- `fetch_pending()` ahora trae `e.geoZone` (heredado de la cuenta publicadora en la creación del evento, mismo campo que ya usa `site/app.js` vía `GEO_LABEL`: `Île-de-France` / `Francia fuera IDF` / `Fuera de Francia`).
- Nuevo selector arriba de la lista para filtrar por esa zona (o "sin dato" para eventos cuya cuenta nunca pasó por `load_manual_account_categorization.py`), y un botón "❌ Rechazar los N eventos visibles" que aplica `reject_bulk()` (mismo mecanismo que el rechazo individual — `:Rejected` + quitar `:PendingReview`, nunca `DETACH DELETE`) a todo lo que quedó filtrado, en una sola operación.
- **Caveat explícito, no resuelto:** `geoZone` describe la cuenta que publicó, no necesariamente la ubicación real del evento — una cuenta parisina podría anunciar un evento en otro país (o viceversa). El filtro es una heurística útil para el caso mayoritario que Diego describió, no una garantía por evento; sigue quedando a su criterio revisar antes de usar el botón masivo.
- Verificado con `py_compile`. No probado contra Neo4j real en este sandbox (sin credenciales).

---

**DD-053 — El deploy del sitio es un paso manual aparte (Cloudflare Workers / Wrangler), no automático (2026-08-25).** Diego corrió `5_export_dashboard_data.py` después de aprobar eventos en `review_events.py` y de hacer `git push`, pero el sitio en vivo seguía sin mostrar los eventos nuevos. Diagnóstico: `site/wrangler.jsonc` confirma que el sitio se sirve como Cloudflare Workers Assets (archivos estáticos de `site/`), desplegado con `npx wrangler deploy` desde esa carpeta — un comando manual, sin ningún trigger automático desde `git push` ni desde correr el script de export. Confirmado con evidencia directa: `git log` mostraba el último commit a `site/data.json` el 18 de agosto (muy anterior a toda la sesión de hoy), mientras que el archivo en disco tenía timestamp de minutos antes (el export sí había corrido bien, solo que nunca se publicó).

- **Segundo hallazgo, más sutil, después de que el primer `wrangler deploy` "no arregló nada":** la URL correcta del sitio es **`https://hub-cultural-du.diegomerchanm.workers.dev`** (con `-du`, coincide con `"name": "hub-cultural-du"` en `wrangler.jsonc`) — no `hub-cultural.diegomerchanm.workers.dev` (sin `-du`), que es un Worker *distinto* que Diego (y yo, verificando) veníamos consultando. El deploy real había funcionado perfecto desde el primer intento; el conteo verificado en `hub-cultural-du.diegomerchanm.workers.dev/data.json` (751 eventos, 349 cuentas) coincidía exacto con el export local — el "sitio desactualizado" era en realidad estar mirando el Worker equivocado. **Pendiente sin resolver:** por qué existe ese segundo Worker sin `-du` y si tiene un dominio custom apuntándole desde el dashboard de Cloudflare — no se investigó, Diego debería revisarlo para no dejar un Worker huérfano confundiendo futuras verificaciones.

- **Fix:** `CLAUDE.md` actualizado — nueva fase 9 del pipeline ("Publish"), separada en 9a (`5_export_dashboard_data.py`, regenera `site/data.json`) y 9b (`cd site && npx wrangler deploy`, el paso que se estaba salteando).
- **No resuelto / desconocido:** no está claro por qué nunca se había corrido `wrangler deploy` antes en ninguna sesión visible para mí — el sitio ya estaba en vivo con datos de antes, así que en algún momento sí se desplegó (posiblemente desde la máquina de Diego, fuera de cualquier sesión de Cowork/Claude, o vía el dashboard de Cloudflare en vez del CLI). No se investigó más a fondo.
- Al correr `wrangler deploy` por primera vez en esta sesión, la CLI detectó agentes de código IA (Claude Code, GitHub Copilot) y ofreció instalar "Cloudflare skills" — Diego aceptó (`Y`), es una función oficial de Wrangler, no representa riesgo.
- **Pendiente:** evaluar si vale la pena automatizar 9a+9b en un solo script/alias, para que este paso deje de olvidarse — mencionado como idea, no implementado todavía (Diego no lo pidió explícitamente, solo documentar el flujo).

---

**DD-054 — El switch ES/FR del sitio ahora también traduce categorías, zonas geográficas, fecha y tags libres (2026-08-25/26).** Diego notó, mirando una captura del filterbar, que el botón ES/FR (i18n.js, ya cubría texto de interfaz desde antes) no alcanzaba las categorías del menú de temas, las pills de "Dónde", ni el formato de fecha — todo seguía en español sin importar el idioma elegido. Investigación: dos causas de raíz distintas.

- **Causa 1 — taxonomía fija (11 categorías) y zona geográfica: bug puro de frontend, sin tocar Neo4j ni el LLM.** `CATEGORY_META` en `app.js` guardaba un solo `label` en español por categoría, y ese mismo string se usaba a la vez como identidad interna del filtro (`STATE.theme`) y como texto visible — no había forma de traducir el texto sin también romper qué pill quedaba "activa". Lo mismo con `GEO_LABEL` (ni siquiera pasaba por `i18n.js`) y con `fmtDate` (array `MONTH_ES` fijo, sin equivalente en francés). Fix: la identidad canónica del filtro se queda en español tal cual estaba (no se tocan los `catWeights` que algún visitante ya tenga guardados en `localStorage`, ver DD de recomendación sin cuentas), pero ahora hay funciones `categoryLabel()`/`geoLabel()`/`themeLabel()` en `app.js` que resuelven el texto a mostrar contra diccionarios nuevos en `i18n.js` (`categories`, `geoLabels`, `months`, uno por idioma) y caen al español si no encuentran traducción — mismo patrón de fallback que `evTitle`/`evDescription` (DD-051). Funciona de inmediato para eventos viejos y nuevos, sin backfill ni costo de LLM. De paso se tradujo "No confirmado" (un cuarto valor de `geoZone` que aparece en la planilla curada además de las tres zonas documentadas en `CLAUDE.md`, encontrado al auditar qué pasaba por el fallback sin traducir de `GEO_LABEL`).
- **Causa 2 — tags libres (`eventArtTags`, DD-042): no son una taxonomía fija, las propone el LLM por evento, y ya había más de 50 valores distintos en el sitio.** Ahí la solución de arriba no alcanza (no hay un diccionario fijo posible). Se evaluaron dos alcances con Diego; eligió el más completo: (1) `4_enrich_events_extract.py` ahora también genera `art_tags_fr` en la misma llamada del LLM que ya genera `title_fr`/`description_fr` (mismo prompt, sin llamadas extra) — alineado por posición con `art_tags`, con un chequeo nuevo que trunca ambas listas al mismo largo si el LLM devuelve una traducción de menos (evita que una traducción se le pegue al tag equivocado en el frontend). Nueva propiedad `:Event.eventArtTagsFr`, seteada solo en la rama de creación de `upsert_event` — mismo patrón creation-only sin backfill que titleFr/descriptionFr. (2) Como eso solo cubre eventos nuevos y el vocabulario de tags ya en el grafo es chico y muy repetido entre eventos, se sumó `backfill_art_tags_fr.py`: junta el vocabulario ÚNICO de tags sin traducir (no evento por evento), lo traduce en una sola llamada a Groq, y aplica el diccionario resultante a todos los eventos que lo necesiten — `--dry-run` muestra el diccionario propuesto antes de escribir. Idempotente (mismo criterio de "necesita backfill" en la lectura del vocabulario y en la escritura: `eventArtTagsFr` ausente o de largo distinto a `eventArtTags`).
- **Frontend:** `app.js` arma un diccionario ES→FR de tags (`TAG_FR_MAP`) recorriendo `DATA.events` una sola vez al cargar (`buildTagFrMap()`), y `themeLabel()` lo usa para traducir un tag libre si existe traducción; si no, cae al español. Las pills de tema (`theme-pills`) y de zona (`geo-pills`) ahora renderizan con `themeLabel()`/`geoLabel()` en vez del string crudo.
- **Fuera de alcance, no tocado:** `ev.artType` (campo de texto libre a nivel de CUENTA, curado a mano en la planilla — distinto de `eventArtTags`, que es por evento y viene del LLM) sigue mostrándose sin traducir en el panel de detalle; traducirlo requeriría traducir la planilla curada, no el pipeline de eventos. Tampoco se tocaron los tooltips de badges ("Puente", "Resonando", "Confirmado x2") — mismo tipo de bug (texto hardcodeado en español en `app.js`), pero no fue parte de lo que Diego pidió esta vez.
- Verificado: `python3 -m py_compile` en los tres scripts Python nuevos/tocados, `node --check` en `app.js`/`i18n.js`, y un harness de Node standalone (fuera del repo) que carga `i18n.js`+`app.js` y ejercita `categoryLabel`/`geoLabel`/`themeLabel`/`fmtDate` en ambos idiomas con datos de evento simulados — confirma que el switch ES↔FR cambia el resultado de las cuatro funciones sin romper la identidad de filtro. No probado contra Neo4j real ni contra Groq real en este sandbox (sin credenciales ni acceso de red) — `backfill_art_tags_fr.py` en particular queda sin correr ni una vez, pendiente de que Diego lo pruebe primero con `--dry-run` en su máquina.
- **Pendiente:** correr `backfill_art_tags_fr.py --dry-run` y revisar el diccionario propuesto antes de aplicarlo; correr una extracción real para confirmar que `art_tags_fr` efectivamente llega parejo con `art_tags` en la práctica (no solo en el smoke test); decidir si vale la pena aplicar el mismo tratamiento a los tooltips de badges y a `artType` de cuenta en algún momento futuro.

---

**DD-055 — Rediseño de jerarquía visual del filterbar: separadores de sección, colores/tamaños por categoría, y fix del bug en celular (2026-08-26).** Diego mandó una captura del sitio ya con el fix bilingüe de DD-054 desplegado y señaló tres problemas de UX, no de traducción: (1) "Dónde" y "Cuándo" no se leían como separadores de sección, se confundían con una opción más; (2) el menú de categorías (50+ entre las 11 fijas y los tags libres del LLM) se veía caótico, todo del mismo tamaño y color; (3) desde el celular "los eventos no cargan". Pidió explícitamente que se le hicieran preguntas antes de tocar nada, dado lo abierto del pedido ("actuá como un diseñador UX experto, que siga siendo simple").

- **Preguntas hechas y respuestas de Diego:** (a) qué hacer con tags de 1-2 eventos → quería algo tipo "Otras categorías" que las siga mostrando, chicas, en vez de ocultarlas del todo; (b) color de los tags libres (las 11 categorías fijas ya tenían uno curado en `CATEGORY_META`) → color automático generado del texto; (c) cómo variar tamaño → 3 tamaños discretos; (d) qué pasa exactamente en el celular → "el menú es tan grande que no se ven los eventos, porque está fijo, pero creo que los eventos están debajo del menú" — autodiagnóstico correcto, confirmado después revisando el CSS (`filterbar-wrap` es `position: sticky`, así que un menú más alto que la pantalla tapa todo lo de abajo).
- **Jerarquía visual (`index.html`, `style.css`):** cada grupo (Dónde/Cuándo/Categoría/Otras categorías) pasó a tener un "chip" de etiqueta oscuro y redondeado que lo marca como separador, visualmente distinto de los pills clickeables claros que contiene — "Otras categorías" usa la misma forma pero contorneada (no rellena) para leerse como subordinada a "Categoría". El filterbar pasó de una sola fila que envolvía todo a filas apiladas por grupo.
- **Categorías por volumen (`app.js`):** se separan por conteo de eventos — más de 2 arman la nube principal con 3 tamaños (`tier-lg/md/sm`, calculados por terciles dinámicos sobre el volumen real de esa corrida, no umbrales fijos que haya que retocar cuando el corpus crezca); 2 o menos van a la fila "Otras categorías" en tamaño `tier-xs`, visibles pero chicas, tal como pidió Diego (no ocultas).
- **Color automático de tags libres:** `hashColor()` genera un color HSL determinístico a partir del texto del tag (mismo tag = mismo color siempre, sin guardar nada, se recalcula igual en cada visita). Aplicado como borde izquierdo + fondo tenue vía `color-mix()` (no relleno sólido) para que se mantenga legible con 50+ variaciones de color a la vez. Las 11 categorías fijas siguen usando su color ya curado en `CATEGORY_META`.
- **Fix del bug en celular:** además de que la reducción de clutter de arriba ya baja mucho la altura real del menú, se agregó una red de seguridad en la media query mobile — `filterbar-wrap` nunca ocupa más del 70% del alto de pantalla, scrollea adentro suyo en vez de tapar todo lo demás, pase lo que pase con el volumen de categorías a futuro.
- Verificado: `node --check` en los 4 archivos JS tocados, balance de llaves en `style.css`, y un harness de Node que corre `render()` completo sobre ~200 eventos simulados sin excepciones, más pruebas puntuales del split principal/otras y de que el color inline de un pill nunca pisa el fondo oscuro del estado `.active` (un riesgo real: un `style` inline tiene más especificidad que cualquier clase CSS). **No visto en un navegador real ni en un celular real** — pendiente de que Diego lo confirme visualmente después de desplegar.

---

**DD-056 — Ajustes tras ver DD-055 desplegado: fusión de sinónimos de geoZone y eliminación de "Otras categorías" (2026-08-26).** Diego mandó capturas del sitio ya desplegado con DD-055 y reportó dos cosas más, además de dos preguntas de diseño abiertas (mapa, panel de detalle — sin resolver todavía, ver conversación).

- **Bug real encontrado auditando `site/data.json` directamente** (no solo mirando el código): la zona "fuera de Île-de-France" existe en el dato real como **tres strings distintos** — `"Francia fuera de IDF"` (12 eventos), `"Francia (fuera de Île-de-France)"` (11 eventos) y por separado `"No confirmado"` (8, zona genuinamente distinta). El diccionario de traducción de DD-054/DD-055 tenía además una clave mal escrita respecto al dato real (`"Francia fuera IDF"`, sin "de" — copiada tal cual del texto de `CLAUDE.md`, que también está impreciso en ese punto) — por eso ni traducía ni fusionaba. Resultado antes del fix: dos pills separados en el filtro para lo que es la misma zona, y encima ninguno traducía en modo FR.
  - **Fix:** `canonicalizeGeoZone()` en `app.js`, un diccionario de sinónimos chico (hoy solo esa una entrada) aplicado UNA vez sobre `DATA.events` al cargar (`init()`), antes de cualquier conteo/filtro/traducción — así todo lo demás en el código sigue trabajando con un solo valor canónico sin tener que tocar cada lugar que lee `ev.geoZone`. Se corrigió también la clave del diccionario de traducción (`"Francia fuera de IDF"`, con "de").
  - **Pendiente, no resuelto:** esto es un parche defensivo en el frontend, no una limpieza de la fuente — la causa real es que la planilla curada tiene texto libre inconsistente para `geoZone`. Si aparecen más variantes a futuro van a necesitar una entrada nueva acá o, mejor, una limpieza directa de la planilla (fuera de alcance de esta sesión, Diego no lo pidió).
- **"Otras categorías" (agregada en DD-055) se sacó del todo.** Diego la vio desplegada y consideró que seguía siendo ruido visual aunque estuviera más chica — y como cada uno de esos eventos ya es encontrable por su categoría fija de todas formas, no aporta lo suficiente como para justificar el espacio. Los tags/categorías con 2 eventos o menos ya no arman pill en el menú (antes: fila aparte más chica; ahora: no se renderizan en absoluto). El evento sigue existiendo y filtrable por su categoría principal — solo ese tag puntual deja de tener su propio botón.
- Verificado: `node --check` en los archivos JS tocados, balance de llaves en `style.css`, y un harness de Node que confirma `canonicalizeGeoZone()` fusiona ambas variantes y que `geoLabel()` traduce correctamente el resultado en FR.

---

**DD-057 — Sacar el mapa, sumar geolocalización opcional, y traer las fotos reales de Instagram a los eventos (2026-08-26).** Diego pidió opinión de diseño sobre tres cosas más, viendo el sitio ya desplegado con DD-055/DD-056: el mapa (tinte de color raro, ya veía venir eso el propio comentario de DD-045), si valía la pena "quitarlo y pedir geolocalización en su lugar", y si había más información disponible que no se estuviera mostrando en el panel de detalle (lo encontraba "pobre").

- **Mapa: sacado del todo.** Diego confirmó explícitamente esa opción tras mi propuesta de simplemente arreglar el filtro CSS. Se borró `ensureMap()`/`renderMap()`, el botón "Ver mapa", `#map-section`, la carga de Leaflet (script + CSS) de `index.html`, y todas las reglas `.map*`/`.leaflet-*` de `style.css`. `STATE.view` desaparece del todo (ya no hay dos vistas).
- **Geolocalización opcional, gesto explícito, en memoria únicamente.** Nuevo botón "Cerca de mí" (`#geo-locate-toggle`) que dispara `navigator.geolocation.getCurrentPosition()` solo al hacer click — nunca automático al cargar la página. Si el visitante concede el permiso, se guarda `STATE.userLocation` (nunca en `localStorage`, ni se manda a ningún lado — vive solo mientras dura la pestaña, coherente con el resto del sitio: sin cuentas, sin backend, sin datos personales) y el orden pasa automáticamente a "Cercanía" (nueva opción en el `<select>` de orden, vía distancia Haversine). Elegir "Cercanía" directamente desde el dropdown sin haber dado permiso todavía también dispara el pedido. Los eventos sin `lat`/`lon` no desaparecen en este modo (a diferencia del mapa viejo, que los ocultaba sin avisar) — se van al final de la lista con distancia infinita. Se muestra la distancia en cada tarjeta cuando este modo está activo.
- **Fotos reales de Instagram (`imageUrl`).** Investigación previa (agente encargado) encontró que `2_build_graph.py` ya captura `p.displayUrl` (la URL de la imagen del post, viene de Apify) desde siempre, pero nunca se propagaba de `:Post` a `:Event` — por eso el sitio mostraba un bloque de color liso en vez de la foto real. Fix en tres partes:
  - `4_enrich_events_extract.py`: la query de posts candidatos ahora trae `p.displayUrl`, y `upsert_event()` lo guarda como `e.imageUrl` — mismo patrón creation-only que `titleFr`/`sourcePostUrl` (nunca se pisa al enriquecer un evento existente).
  - `5_export_dashboard_data.py`: `EVENTS_QUERY` ahora exporta `e.imageUrl`.
  - `backfill_event_images.py` (nuevo, sin LLM): copia `p.displayUrl` a `e.imageUrl` para los 751 eventos ya existentes, vía la relación `(:Post)-[:MENTIONS_EVENT]->(:Event)` que ya existía — si un evento tiene varios posts asociados, toma cualquiera de las URLs no vacías. Idempotente, `--dry-run` disponible.
  - Frontend (`app.js`/`style.css`): `imageBlockHtml()`/`attachImageFallback()` — si `ev.imageUrl` existe se muestra como foto real (`<img>`, `object-fit: cover`); si falta, o si la URL falla al cargar, degrada al diseño de color+ícono de siempre. Necesario porque las URLs de la CDN de Instagram están firmadas y pueden expirar con el tiempo — no hay garantía de que sigan sirviendo para siempre, así que el fallback no es opcional.
- **Pendiente:** correr `backfill_event_images.py` (requiere credenciales Neo4j, no disponibles en este sandbox) y confirmar visualmente que las fotos cargan bien en un navegador real — no verificado, solo la lógica con datos simulados. El rediseño del panel de detalle (mostrar `institutionType`/`culturalIdentity`/`parentInstitution`, ya exportados pero sin usar; reorganizar el layout para depender menos de scroll en mobile) quedó para un mockup aparte antes de tocar código real, a pedido explícito de Diego.
- Verificado: `py_compile` en los 3 scripts Python, `node --check` en `app.js`/`i18n.js`, balance de llaves en `style.css`, y varios harnesses de Node que ejercitan `requestLocation()`/`haversineKm()`/orden por distancia con datos simulados (incluyendo el hallazgo de que Node 22 expone un `navigator` global no reescribible, lo que hizo falta sortear con `Object.defineProperty` en el propio harness de prueba — no afecta el código del sitio, que corre en un navegador real donde `navigator` sí es el objeto real) y la lógica de fallback de imagen.

---

**DD-058 — Rediseño del panel de detalle de evento: pestañas Resumen/Más info, chips de identidad/institución/zona, sin grid de dos columnas (2026-08-26).** Quedaba pendiente de DD-057 el rediseño del panel de detalle (mostrar `institutionType`/`culturalIdentity`/`parentInstitution`, ya exportados desde hace tiempo pero nunca usados en el frontend, y reducir la dependencia de scroll en mobile). Diego pidió explícitamente un mockup antes de tocar código real; se presentó un mockup (widget, sin código de sitio) con un patrón de dos pestañas dentro del panel — aprobado, se pasó a implementación real.

- **Patrón elegido: pestañas "Resumen" / "Más info" en vez de scroll único.** El `.detail-grid` de dos columnas (contenido principal + sidebar de 220px, que en mobile colapsaba a una sola columna larga) se reemplazó por un layout de una sola columna con dos paneles conmutables por click (`data-tab`/`data-pane`, sin recargar ni tocar `STATE`). "Resumen" trae lo esencial para decidir si ir al evento sin scrollear: foto, fecha, título, una fila nueva de chips (identidad cultural, tipo de institución, zona geográfica), descripción, caja de dirección/ciudad/precio, y el CTA a la publicación original. "Más info" agrupa lo secundario que antes competía por espacio arriba del pliegue: card del organizador (ahora con `parentInstitution` agregado), la razón de recomendación (`why-box`), los tags de `artType`, eventos similares, y el detalle técnico de detección (`<details>`, ya colapsado por defecto desde antes).
- **Campos nuevos mostrados por primera vez:** `culturalIdentity` e `institutionType` como chips en "Resumen" (`.chip-identity`/`.chip-institution`, colores derivados de `--blue`/`--yellow` vía `color-mix()` para no introducir una paleta nueva) y `parentInstitution` como línea de texto dentro de la card del organizador en "Más info". Los tres ya viajaban en `data.json` desde la carga de categorización manual — el gap era puramente de frontend, no de datos ni de pipeline.
- **`geoZone` deja de duplicarse:** antes aparecía tanto en el `tag-row` de la sidebar como potencialmente en otro lugar; ahora vive una sola vez, como chip en "Resumen" (`chip-geo`, mismo `geoLabel()` de siempre). El `tag-row` de "Más info" ahora es solo para `artType` y se omite del todo si la cuenta no tiene ese campo curado (antes siempre renderizaba el contenedor, aunque quedara vacío).
- **i18n:** nuevas keys `tabSummary`/`tabMoreInfo`/`cultIdLabel`/`instTypeLabel`/`parentInstitutionLabel` en `i18n.js`, ambos idiomas — mismo patrón de diccionario por idioma que el resto del archivo.
- **CSS:** se borró `.detail-grid` (regla base y su override en la media query mobile, ya no aplica con una sola columna) y `.detail-panel` bajó de `max-width: 720px` a `560px` — pensado para dos columnas, se veía innecesariamente ancho como columna única. Nuevas reglas `.detail-tabs`/`.detail-tab`/`.detail-tab.active`/`.detail-pane.hidden`/`.chip-row`/`.chip*`.
- Verificado: `node --check` en `app.js`/`i18n.js`, balance de llaves en `style.css` (104/104), grep confirmando que no quedó ninguna referencia muerta a `.detail-grid`, y un chequeo de estructura que confirma que todos los hooks (`data-tab`, `data-pane`, clases de chip) que el JS espera están efectivamente en el HTML que genera `openDetail()`.
- **Pendiente:** no visto en un navegador real — falta que Diego lo confirme visualmente (desktop y mobile) después de desplegar, y decidir si el mismo tratamiento de chips vale la pena extenderlo a la tarjeta de evento en la grilla (hoy los chips solo aparecen al abrir el detalle).

---

**DD-059 — Exploración de HikerAPI como alternativa a Apify para scraping de posts, sin calibrar aún en vivo (2026-08-27).** Diego quiere comparar el costo de HikerAPI (pay-per-use, sin el fee fijo de ~$30 USD que cobra Apify) contra el actor `apify/instagram-post-scraper` que hoy corre `1_harvest_ig_posts.py`, y dejar la posibilidad de elegir uno u otro según convenga. Ya existía precedente para *cuentas* (DD-049, `1_harvest_ig_network_hikerapi.py`, ~25x más barato que Apify para descubrimiento de red) pero nunca se había probado el endpoint de *posts*.

- **Decisión de arquitectura (elegida por Diego entre las opciones planteadas):** script aparte, `1_harvest_ig_posts_hikerapi.py`, en vez de meter un flag `POSTS_SCRAPER=apify|hikerapi` dentro de `1_harvest_ig_posts.py` — mismo patrón que ya existe para el scraper de red (DD-049), no toca el script de Apify que ya funciona.
- **Endpoint encontrado:** `GET /v1/user/medias/chunk?user_id=...&end_cursor=...` (la doc pública de HikerAPI no lo lista bajo "Media", está bajo "User" — tuvo que buscarse la tabla de contenidos completa para encontrarlo). Según la doc, la respuesta es `[lista_de_media, end_cursor_o_null]` — el mismo patrón "lista de dos" que ya se vio en `/v1/user/following/chunk` (DD-049), donde la doc pública **no coincidía** con la respuesta real. Por eso el parseo en el script es defensivo (acepta esa forma o un dict `{"items":...}`), pero **no se pudo confirmar contra una llamada real**: el sandbox donde se escribió esto no tiene salida de red hacia `api.hikerapi.com` (`ProxyError: Tunnel connection failed: 403 Forbidden` al intentarlo) — el mismo tipo de bloqueo de red que ya afecta a Neo4j desde este entorno, documentado en turnos anteriores. Este es el motivo por el que las tareas de calibración real y de comparación de costo contra cuentas reales quedaron sin ejecutar en esta sesión — Diego tiene que correrlas él mismo.
- **Diseño del script — normalización a un shape idéntico al de Apify:** en vez de introducir un formato de datos nuevo, `normalize_media()` mapea cada campo de HikerAPI al mismo nombre de propiedad que ya usa `posts_<username>.json` (Apify), para que `2_build_graph.py` pueda ingerir cualquiera de los dos sin ningún cambio. Mapeo revisado campo por campo contra `2_build_graph.py::load_posts()`:
  - `id`/`shortCode`/`caption`/`timestamp`/`likesCount`/`commentsCount`/`videoViewCount`/`videoPlayCount`/`videoDuration`/`isCommentsDisabled`/`coauthorProducers` mapean 1:1 desde `pk`/`code`/`caption_text`/`taken_at`/`like_count`/`comment_count`/`view_count`/`play_count`/`video_duration`/`comments_disabled`/`coauthor_producers`.
  - `displayUrl`: HikerAPI no tiene un campo con ese nombre exacto — se aproxima con `image_versions[0].url` (fotos/carruseles) o `thumbnail_url` (videos/reels) como fallback. Mismo propósito (imagen de portada) que usa el pipeline de eventos y el `imageUrl` de `:Event` (DD-057/058), pero no se verificó que sea *exactamente* la misma resolución/crop que devuelve el actor de Apify.
  - `type`: HikerAPI trae `media_type` como entero (1/2/8); se mapea a los strings que usa Apify (`"Image"/"Video"/"Sidecar"`) solo por legibilidad — un grep contra `2_build_graph.py` y `4_enrich_events_extract.py` confirmó que nada del pipeline activo filtra por `p.type`, así que un mapeo imperfecto acá no tiene impacto funcional.
  - `hashtags`/`mentions`: HikerAPI no los devuelve pre-parseados como listas de entidades (Apify sí) — se reconstruyen con una regex simple sobre `caption_text`. Aproximación razonable, no idéntica al parser de entidades de Instagram.
  - `musicInfo`/`latestComments`: no existen en este endpoint (necesitarían llamadas pagas extra por post — `/v1/media/comments/chunk` para comentarios, y no se encontró endpoint de música). Se dejan vacíos a propósito — un grep contra `4_enrich_events_extract.py` confirma que la extracción de eventos no lee ninguno de los dos campos hoy, así que el impacto real es bajo. Si el pipeline llega a necesitarlos en el futuro, este script hay que revisarlo.
- **Ajuste tras la primera versión (mismo día, a pedido de Diego): corte temprano de paginación por fecha.** La primera versión de `harvest` bajaba siempre el tope completo (`RESULTS_LIMIT`, 50 posts) y recién después filtraba por `--max-days` — desperdiciaba requests pagos en páginas que se iban a descartar igual. `fetch_medias_raw()` ahora acepta un `cutoff` opcional y corta la paginación apenas una página entera queda más vieja que ese corte (asumiendo que el feed viene ordenado de más nuevo a más viejo, como es lo normal en Instagram — no confirmado aún con una llamada real, mismo caveat que el resto del script). Verificado con un smoke test que simula 3 páginas donde la segunda cruza el cutoff: confirma que se cortan las requests en la página 2 (nunca pide la página 3) y que el post fuera de ventana se descarta. El reporte de `compare` también ahora muestra el rango de fechas real (más vieja/más nueva) de los posts de Apify y de HikerAPI, no solo la cantidad.
- **Tres subcomandos, pensados para no gastar de más antes de confiar en el endpoint:** `calibrate` (una sola cuenta, 1 página, JSON crudo sin normalizar, ~$0.001 USD — para que Diego confirme a mano si la forma real coincide con lo que dice la doc), `compare` (agarra una cuenta que YA tiene `posts_<username>.json` de Apify, pide la misma cantidad vía HikerAPI, guarda en `posts_hikerapi_<username>.json` — nunca pisa el archivo de Apify — e imprime solapamiento de ids + cobertura de campos + costo real vs. el promedio histórico de Apify para esa cuenta en `.apify_cost_log.json`), y `harvest` (modo producción: escribe `posts_<username>.json` normalizado, filtro de recencia `--max-days` client-side ya que no se encontró un parámetro server-side equivalente a `onlyPostsNewerThan`, incremental por defecto igual que el resto del pipeline).
- **FinOps:** reutiliza `.hikerapi_cost_log.json` (mismo archivo que ya usa el scraper de red, DD-049) pero cada entrada ahora lleva un campo `type` (`"posts"` vs. el patrón implícito de las corridas de red) para no mezclar promedios de corridas de naturaleza distinta al leer el histórico. `PRICE_PER_REQUEST = 0.0006` se asume igual al ya calibrado para `/v1/user/following/chunk` (no está en la tabla de "multi-request endpoints" de la doc de costos de HikerAPI, así que 1 request/página es razonable) — pero, igual que la forma de la respuesta, no se confirmó con una llamada real.
- Verificado: `py_compile`, y un smoke test standalone en Python que ejercita `normalize_media()` contra el JSON de ejemplo de la doc oficial de HikerAPI (asserts sobre `id`/`shortCode`/`displayUrl`/`hashtags`/`mentions`/`timestamp`) y el parseo defensivo de `_parse_chunk_response()` contra ambas formas posibles (`[items, cursor]` y `{"items":...}`) — sin hacer ninguna llamada de red real.
- **Pendiente, bloqueante antes de usar `harvest` en serio:** Diego tiene que correr `calibrate` contra 1 cuenta real desde su máquina (fuera de este sandbox) para confirmar la forma de la respuesta, y `compare` contra 2-3 cuentas con `posts_<username>.json` de Apify ya existentes (se identificaron `francy_barahona_calisabor`, `consuladocolparis` y `laroutedurock` como candidatas — volumen medio, distintos tipos de cuenta) para tener una cifra real de costo y cobertura de campos antes de decidir si HikerAPI reemplaza a Apify como scraper de posts por defecto, convive como alternativa, o se descarta.

---

**DD-060 — Permiso explícito de foto por cuenta: dos niveles de exposición visual (foto real vs. embed oficial de Instagram) según consentimiento (2026-08-27).** Cierre de la consulta legal/reputacional de las últimas sesiones (derechos de autor sobre las fotos de Instagram embebidas, fallo Renckhoff sobre republicar vs. enlazar, riesgo que crece con la escala a más ciudades). Diego decidió el escenario: por defecto usar el embed oficial de Instagram (que no aloja ninguna copia propia del archivo), y reservar la foto real, más grande y sin el branding de Instagram, para las cuentas que autoricen explícitamente — aprovechando que la curación manual ya es 1-a-1 por cuenta, así que pedir permiso es un campo más en esa misma planilla, no un proceso nuevo.

- **Dato nuevo, fail-closed por diseño:** columna 14 de `cuentas_instagram_completo_v4.xlsx` ("Permiso de foto", texto libre Sí/No). `_is_yes()` en `load_manual_account_categorization.py` normaliza a `True`/`False`/`None` — `None` (cuenta todavía sin contactar) y `False` (dijo que no) se tratan exactamente igual en el sitio: sin autorización explícita y positiva, nunca se muestra la foto real. Escrito como `a.photoPermission` en `:Account`, vía `MERGE`/`SET` en el mismo patrón idempotente que el resto de la categorización manual.
- **Herencia a `:Event` sin costo de LLM:** `4_enrich_events_extract.py` copia `photoPermission` de la cuenta publicadora al crear el evento — mismo patrón creation-only, sin backfill automático, que ya usan `artType`/`institutionType`/`culturalIdentity`/`geoZone`/`parentInstitution` (la query de candidatos y el dict de `candidate` se extendieron con el campo, la rama de creación de `upsert_event` lo escribe). `5_export_dashboard_data.py` lo exporta tal cual (`e.photoPermission AS photoPermission`), sin transformación — el paso final que arma el JSON hace `dict(record)` directo, así que no hizo falta tocar nada más ahí.
- **Frontend — tres estados, no dos:** `hasPhotoPermission(ev)` (`site/app.js`) es la única fuente de verdad de si se puede mostrar `ev.imageUrl`; se usa tanto en tarjetas/hero (`imageBlockHtml`, ya existía, solo se le agregó el chequeo) como en el panel de detalle. La novedad es el panel de detalle: antes cualquier evento sin `imageUrl` (o con la cuenta sin autorizar) caía al placeholder de color+ícono; ahora `detailMediaHtml(ev, meta)` decide entre tres estados — `"photo"` (autorizado, foto real como hasta ahora), `"embed"` (no autorizado pero hay `sourcePostUrl`: se inserta el `<blockquote class="instagram-media">` oficial de Instagram, que renderiza el post en vivo desde los servidores de Instagram, sin alojar ninguna copia propia — el mecanismo que el propio Instagram ofrece para este caso exacto, no un scrape), o `"icon"` (ni foto ni URL de origen: el placeholder de siempre). El script `embed.js` de Instagram se carga una sola vez, perezoso (`loadInstagramEmbedScript()`, solo la primera vez que hace falta, no en la carga inicial del sitio) y se reprocesa con `window.instgrm.Embeds.process()` cada vez que se abre un evento en modo embed.
- **Por qué no hace falta una lógica de fallback propia para el embed:** el `<blockquote>` que arma `detailMediaHtml()` ya trae adentro un `<a href="...">` real al post — es el contenido de reserva que el propio formato de Instagram define. Si `embed.js` no carga (red, adblocker) o Instagram no puede procesar el post (borrado, cuenta puesta privada), el visitante igual ve un link funcional en vez de una caja vacía o rota — no hizo falta escribir detección de fallo a mano, a diferencia del `onerror` que sí existe para las fotos con URL firmada que pueden expirar (DD-057).
- Verificado: `py_compile` en los tres scripts Python tocados, `node --check` en `app.js`, balance de llaves en `style.css` (107/107), y un smoke test en Node que ejercita `detailMediaHtml()` con los cinco combos relevantes de `photoPermission`/`imageUrl`/`sourcePostUrl` (autorizado con foto, no autorizado con URL de origen, sin dato con URL de origen, ni foto ni URL, autorizado pero sin `imageUrl` real) y otro que confirma que `imageBlockHtml()` en tarjetas/hero nunca muestra la foto salvo con `photoPermission === true` explícito.
- **Pendiente:** Diego tiene que agregar la columna 14 a la planilla real y empezar a contactar cuentas para pedir el permiso — hasta que eso pase, **todos** los eventos (nuevos y viejos, porque los viejos ni siquiera tienen la propiedad) van a mostrar el embed de Instagram en el panel de detalle en vez de la foto propia, que es exactamente el comportamiento conservador que se buscaba por defecto. No se corrió contra Neo4j real (sin credenciales en este sandbox) ni se vio el embed renderizado en un navegador real — falta que Diego lo confirme visualmente después de desplegar.

---

**DD-061 — Embed de Instagram reubicado debajo del contenido propio, y mapa embebido de Google en el panel de detalle (2026-08-27).** Dos pedidos puntuales de Diego tras ver DD-060 desplegado: (1) que el post embebido de Instagram no sea lo primero que se ve al abrir un evento, para que se lea primero la info propia (fecha, título, descripción, dirección); (2) ideas para embeber más información útil, con el mapa como ejemplo concreto.

- **Reposición del embed:** antes, `detailMediaHtml()` decidía qué iba en `.detail-img`, el bloque destacado arriba del todo (foto real, embed de Instagram, o ícono+color) — ahora ese bloque de arriba (`topMedia`) nunca es el embed: si `media.kind === "embed"`, arriba se muestra el mismo ícono+color que el caso "sin foto ni URL", y el `<blockquote class="instagram-media">` real se inserta al final del tab "Resumen", en un nuevo `<div class="detail-embed">`, después del link "Ver publicación original". La foto real autorizada (`kind === "photo"`) sigue arriba sin cambios — ahí sí funciona bien como imagen destacada. CSS: se retiraron las reglas `.detail-img-embed` (ya no aplican, el embed no vive más adentro de `.detail-img`) y se agregó `.detail-embed` con el mismo tratamiento visual del blockquote.
- **Mapa embebido (Google Maps Embed API):** confirmado con Diego que la API es gratis con uso ilimitado, pero igual requiere una API key de un proyecto de Google Cloud con facturación activada (sin cobro real, pero sí cuenta creada) — se evaluó como alternativa gratis-sin-cuenta un iframe de OpenStreetMap (que además calzaría con que `4_enrich_locations.py` ya usa Nominatim, el geocoder de OSM), pero Diego prefirió Google por el resultado visual. Implementación: `mapEmbedHtml(ev)` en `app.js` arma un `<iframe>` a `https://www.google.com/maps/embed/v1/place` usando `lat`/`lon` del evento si existen (más preciso, ya vienen geocodificados) o si no, la dirección/ciudad como texto de búsqueda; devuelve `""` (no rompe nada) si falta la key o si el evento no tiene ni coordenadas ni dirección. Se inserta en el tab "Resumen", justo después del `info-box` de dirección/ciudad/precio, antes del link "Ver publicación original".
- **La key vive en `site/config.js` (nuevo archivo), no en `app.js`.** `window.GOOGLE_MAPS_EMBED_KEY`, con instrucciones paso a paso en el propio archivo (crear proyecto, activar Maps Embed API, activar facturación, generar key, **restringirla por HTTP referrer al dominio real del sitio** — una key de Maps del lado del navegador siempre queda visible en el HTML, la restricción por dominio es lo que impide el uso indebido, no el ocultamiento). Cargado en `index.html` antes que `i18n.js`/`app.js`. Placeholder `"YOUR_KEY_HERE"` por defecto — con eso puesto, `mapEmbedHtml()` devuelve `""` y el sitio funciona exactamente igual que antes, sin mapa. Diego tiene que generar su propia key y pegarla ahí; no es algo que se pueda hacer desde este sandbox.
- **Nota de continuidad con DD-057:** ese DD había sacado del todo un mapa anterior (Leaflet, pines de TODOS los eventos a la vez, con un toggle de vista completo) por un bug visual y como parte de una simplificación general ("sin cuentas, sin backend, sin datos personales" reforzado en la misma entrega). Este mapa es distinto en alcance: un solo lugar, dentro del panel de un evento ya abierto, sin librería nueva ni estado global — no es una reincorporación de lo que se sacó, pero vale dejarlo anotado por si en el futuro se evalúa si tiene sentido combinarlos.
- Verificado: `node --check` en `app.js`/`i18n.js`/`config.js`, balance de llaves en `style.css` (109/109), y un smoke test en Node de `mapEmbedHtml()` con los cinco casos relevantes (sin key, key placeholder, con lat/lon, con dirección de texto sin lat/lon, evento sin ningún dato de ubicación) — todos devuelven el HTML esperado o `""` según corresponda. **No visto en un navegador real** — el mapa en particular no se puede probar de verdad sin una key real de Diego; falta que la genere, la pegue en `config.js`, despliegue, y confirme visualmente.

---

**DD-062 — Dos botones de acción en el detalle: "Cómo llegar" y "Agregar al calendario" (2026-08-27).** A raíz de la pregunta de Diego sobre costos de la API de mapas, aprobó agregar estos dos como enriquecimiento adicional del panel de detalle. Ninguno usa una API paga ni necesita key: ambos son esquemas de URL pública documentados por Google (Universal Maps URLs para direcciones, plantilla de Google Calendar para el evento) — un simple `<a href>`, igual de "gratis" que un link cualquiera.

- `directionsUrl(ev)` (`app.js`): `https://www.google.com/maps/dir/?api=1&destination=...` — usa `lat`/`lon` si el evento los tiene (más preciso), si no cae a `exactAddress`/`locationName`/`cityName`.
- `gcalUrl(ev)`: plantilla `https://calendar.google.com/calendar/render?action=TEMPLATE&...`. Como `eventDate` en este proyecto es solo fecha (sin hora — la extracción actual no la captura con confianza suficiente, ver limitaciones ya documentadas), se arma como evento de día completo; Google Calendar exige la fecha de fin *exclusiva*, por eso `gcalUrl` suma un día a la fecha de inicio antes de formatear. `details` incluye la descripción del evento más el link al post original si existe, `location` reusa el mismo texto de dirección que el resto del panel.
- Ambos links devuelven `null` (no se renderiza el botón) si el evento no tiene el dato mínimo necesario (`eventDate` para calendario, algo de ubicación para direcciones) — mismo patrón defensivo que `mapEmbedHtml`.
- Se agrupó junto con el link "Ver publicación original" existente en un nuevo `<div class="action-row">` (antes ese link no tenía contenedor propio), después del mapa.
- Verificado: `node --check` en `app.js`/`i18n.js`, balance de llaves en `style.css` (110/110), smoke test en Node de `directionsUrl`/`gcalUrl` con casos de lat/lon, solo dirección, sin ubicación, y sin fecha.

---

**DD-063 — Botón "Compartir" por evento: primer link propio del sitio (2026-08-27).** Diego preguntó si tenía sentido un botón de compartir y si el sitio ya era "una página real" para eso. Aclaración de esa duda: sí, el sitio ya es real y en vivo una vez desplegado — lo que faltaba era otra cosa, que abrir un evento nunca cambiaba la URL de la barra de direcciones, así que hasta ahora "compartir" solo podía compartir el home a secas, sin apuntar al evento puntual. Esta entrega agrega ese link propio por evento, primero de su tipo en el sitio.

- **`eventShareUrl(ev)`** (`app.js`): arma `<origin><pathname>?evento=<id>` — un query string sobre la misma `/`, no una ruta nueva, así que no hace falta tocar `wrangler.jsonc` ni la config de Cloudflare Workers Assets.
- **`openDetail(ev, opts)`** y **`closeDetail(opts)`** ahora aceptan `{ updateHistory }` (default `true`). Al abrir un evento por click normal, `openDetail` empuja la URL con `history.pushState` (sin recargar la página); al cerrar, `closeDetail` la limpia de vuelta a `pathname` si había un `?evento=` puesto. Un listener de `popstate` nuevo maneja el botón atrás/adelante del navegador sobre esas entradas de historial, llamando a `openDetail`/`closeDetail` con `updateHistory: false` (la URL ya la cambió el navegador solo, no hay que volver a pushear encima).
- **`init()`** revisa `?evento=` en la URL apenas carga `data.json` — si el id existe en `DATA.events`, abre ese evento directo (con `updateHistory: false`, ya viene así desde afuera). Esto es lo que hace que un link compartido funcione para quien lo recibe.
- **`shareEvent(ev, btn)`:** usa el Web Share API nativo (`navigator.share`) cuando el navegador lo soporta (típicamente celular — abre el panel nativo con WhatsApp/Instagram/etc. como opciones), con fallback a copiar el link al portapapeles (`navigator.clipboard.writeText`) en navegadores de escritorio sin soporte; el botón muestra brevemente "¡Link copiado!" (`flashShareFeedback`) como confirmación visual. Si el visitante cancela el panel nativo, o el portapapeles está bloqueado por el navegador, se degrada en silencio — mismo criterio de "nunca romper la UI por un permiso de navegador denegado" que ya usa el resto del sitio (geolocalización, DD-057).
- Verificado: `node --check` en `app.js`/`i18n.js`, balance de llaves en `style.css` (112/112), y un test de Node que confirma el round-trip de `eventShareUrl` → `URLSearchParams(...).get("evento")` recupera el mismo id, y que un query string sin `evento` (visita normal al home) no dispara nada. **No probado en un navegador real** — en particular el Web Share API y el fallback de portapapeles no se pueden ejercer fuera de un navegador de verdad; falta que Diego lo pruebe en celular (donde debería aparecer el panel nativo) y en escritorio (donde debería copiar el link) después de desplegar.

---

**DD-064 — Panel de control de la pipeline, v1: correr scripts desde el navegador, con logs e historial (2026-08-27).** Diego pidió algo similar a `review_events.py` pero para operar toda la pipeline: correr cualquier script con sus variantes (dry-run, argumentos), ver el estado de todo, y llevar un registro de lo que pasó. Decisión de alcance con Diego: (1) va como pestaña nueva DENTRO de `review_events.py` (un solo `streamlit run`), no una app separada; (2) v1 chico — correr scripts + logs en vivo + historial; el dashboard de estado (conteos en vivo desde Neo4j) y el botón de deploy (`wrangler deploy`) quedan para una entrega futura.

- **Decisión de diseño central: los argumentos de cada script NO están hardcodeados.** `control_panel.py` (nuevo) lee el propio `typer.Typer()` de cada script por introspección (`discover_variants()`, vía `typer.main.get_command()` + iterar `.params` del objeto Click subyacente) y arma el formulario solo. Confirmado con los 24 scripts reales del repo: **todos** usan Typer (ninguno usa `argparse` crudo) — hallazgo útil, hace que la introspección genérica sea viable para toda la pipeline sin excepciones de sintaxis. Ventaja concreta: si mañana se agrega un `--nuevo-flag` a `4_enrich_events_extract.py`, aparece solo en el panel la próxima vez que se recarga la página — no hay una lista paralela de argumentos que Diego (o yo) tengamos que acordarnos de actualizar. Lo único mantenido a mano es `SCRIPT_REGISTRY`: qué scripts aparecen, en qué fase, con qué descripción — una decisión editorial, no algo automatizable, y agregar un script nuevo es una línea.
- **Dos bugs reales encontrados corriendo la introspección contra los scripts de verdad (no hipotéticos, confirmados con ejecución real en este sandbox):**
  1. Típer agrega automáticamente `--install-completion`/`--show-completion` (helpers de autocompletado de shell) a cualquier app de un solo comando que no pase `add_completion=False` — sin filtrarlos aparecían como checkboxes falsos en el formulario. Confirmado en `5_export_dashboard_data.py`, `load_manual_account_categorization.py`, `seal_legacy_batch.py`, `cleanup_legacy_accounts.py`. Fix: `_SKIP_PARAM_NAMES` filtra ambos por nombre en `_extract_params()`.
  2. `2_build_graph.py` abre la conexión a Neo4j **a nivel de módulo**, no adentro de una función — importarlo (que es lo que hace la introspección) intenta conectar a Neo4j de inmediato. En este sandbox eso falla por falta de red (`ServiceUnavailable`); en la máquina real de Diego probablemente conectaría con éxito, pero de forma innecesaria y silenciosa cada vez que se abre esa entrada del panel. Como este script no tiene Typer ni argumentos de todas formas, se lo marcó `"no_args": True` en el registro — el panel nunca lo importa para leer argumentos, lo corre directo.
- **Scripts con confirmación interactiva por teclado (`input()`), sin ningún `--dry-run` que la evite — no se pueden correr sin colgarse desde un subprocess sin terminal real:** `extract_profiles.py` (pide confirmar el costo estimado de Apify, siempre) queda marcado `"interactive_only": True` — el panel muestra la nota pero no ofrece botón de correr. `cleanup_legacy_accounts.py` sí tiene `--dry-run` (rollback, sin `input()`), pero su corrida real pide escribir "BORRAR" por teclado — marcado `"force_dry_run_only": True`, el panel fuerza `--dry-run` siempre y deshabilita el checkbox, con nota explicando que la corrida real todavía se hace a mano en una terminal. Backlog explícito para una entrega futura: pipear la confirmación desde el propio formulario web (mover la garantía de "un humano confirma antes de la acción irreversible" del terminal al navegador) en vez de bloquear la corrida real desde acá — no se hizo en v1 para no arriesgar un pipe frágil a la primera entrega.
- **Ejecución y logs:** cada corrida es un `subprocess.Popen` (comando `[sys.executable, script, subcomando?, *args]`, `cwd` = raíz del repo) con `stdout`/`stderr` redirigidos a un archivo en `.pipeline_runs/<run_id>.log` (nueva carpeta, gitignored). El panel muestra el log tabulado con un botón manual de "Actualizar" (sin dependencia nueva tipo autorefresh, alcance chico a propósito) mientras el proceso sigue vivo, y el resultado final (éxito/error, código de salida) cuando termina.
- **Historial:** `.pipeline_runs/history.jsonl` (append-only, gitignored) — un registro por corrida: script, subcomando, argumentos, si fue dry-run, cuándo empezó/terminó, estado, código de salida, ruta del log. El panel muestra el historial reciente del script seleccionado más un expander con el historial completo de todos.
- **Limitación conocida de v1, explícita:** el seguimiento en vivo de un job (log, estado "corriendo") vive en `st.session_state` de esa pestaña del navegador — si Diego la cierra, el proceso en sí sigue corriendo en su máquina hasta terminar (no se mata), pero el panel deja de poder mostrar su progreso en vivo hasta que la reabra; el log completo queda igual en el archivo. No se implementó tracking de PID entre sesiones (frágil multiplataforma, Diego está en Windows) para no meter esa complejidad en la primera entrega.
- **`review_events.py`:** el cuerpo existente se movió a `render_review_tab()` (los `st.stop()` pasaron a `return`, porque `st.stop()` corta la ejecución de TODO el script, no solo de una pestaña — hubiera roto la pestaña de al lado). `st.tabs(["🗂️ Revisión de eventos", "🎛️ Panel de control"])` arriba, cada pestaña llama a su propia función. Cero dependencias nuevas: `streamlit`, `typer`, `click`, `pandas` ya estaban en `requirements.txt`.
- Verificado: `py_compile` en ambos archivos; un harness de Node... no, de Python con un stub mínimo de `streamlit` (sin instalarlo de verdad) que ejercita `discover_variants()` contra 12 scripts reales del repo (confirmando los dos bugs de arriba y que el resto introspecciona bien, incluyendo los multi-comando como `1_harvest_ig_posts_hikerapi.py`), `_build_args()` con casos de flags/valores/vacíos, y un ciclo completo real de `_launch()` → subprocess de verdad → log → historial → `_update_history_status()` contra un script señuelo. **No visto en un navegador real** ni contra Neo4j real — falta que Diego corra `pip install -r requirements.txt` (ya cubierto, sin deps nuevas) y `streamlit run review_events.py` para confirmar la pestaña nueva.

---

**DD-065 — Buscador de texto libre + "Este finde" en Cuándo (2026-08-27, Etapa 0 de la conversación sobre arquitectura de URLs/SEO).** Diego propuso una reestructuración grande del sitio (URLs de carpeta país/ciudad, menú tipo Meetup, subcategorías por página) para mejorar "SEO para IA". Antes de tocar nada de eso, análisis compartido con él (no en este DD, en la conversación): confirmado por búsqueda que a mediados de 2026 GPTBot/ClaudeBot/PerplexityBot no ejecutan JavaScript -- solo leen el HTML inicial, así que URLs bonitas sobre el sitio actual (100% renderizado en cliente) no aportarían nada a esos crawlers sin generación estática real (SSG) con estructura de datos, que es un problema aparte y más grande. Además, auditando `site/data.json` real: `geoZone` le falta al 75% de los eventos (561/751) y `cityName` al 52% (395/751) y encima duplicado sin fusionar ("Paris" 75 vs "París" 91) -- un menú país/ciudad tipo Meetup dejaría a más de la mitad del catálogo sin dónde aparecer. En cambio `category` (los 11 temas fijos que ya usa el sitio) está 100% poblado y limpio -- confirma que la categorización SÍ está estandarizada (responde la pregunta de Diego), buena base para páginas por tema más adelante. Se armó un plan en 4 etapas (0: quick wins sin dependencias: buscador + "este finde"; 1: limpieza de geo/ciudad; 2: pipeline SSG + JSON-LD Event; 3: URLs de carpeta + menú país/ciudad) y Diego eligió empezar por la Etapa 0, sin comprometerse todavía a las etapas más grandes.

- **Buscador (`app.js`):** `matchesSearch(ev, query)` compara contra título, descripción, `locationName`/`cityName`/`exactAddress`, `sourceAuthor`, `artType` y `eventArtTags` -- cliente-side puro, sin backend, sobre `DATA.events` ya cargado. `normalizeSearch()` saca acentos (`normalize("NFD")` + strip de diacríticos) antes de comparar, para que "musica" encuentre "música" y viceversa -- pensado para un sitio bilingüe ES/FR donde el visitante puede escribir sin tildes en cualquiera de los dos idiomas. Nuevo campo `STATE.search`, integrado en `applyFilters()` junto al resto de los filtros (geo/cuándo/tema/gratis). Input `#search-input` nuevo en `index.html` (fila propia arriba de "Dónde" en el filterbar), no se reconstruye en cada render -- `renderFilterBar()` solo sincroniza `.value` (no pisa lo que el visitante está escribiendo, la asignación es no-op cuando ya coincide) y cablea `oninput`, mismo patrón que los toggles de gratis/geolocalización ya existentes. `applyStaticI18n()` se extendió para soportar `data-i18n-placeholder` (el placeholder del input también se traduce ES/FR).
- **"Este finde" (`app.js`):** `isWeekendEvent(dateStr)` -- función nueva, separada de `whenBucket()` a propósito (esa devuelve un bucket mutuamente excluyente por evento y ya la usan las pills de hoy/semana; mezclar "weekend" ahí rompería esa exclusividad, un evento de sábado tiene que seguir contando como "week" también). Sábado y domingo más próximos sin caer en el pasado: si hoy es sábado, el finde es hoy+mañana; si es domingo, el finde ya empezó y termina hoy (no se resucita el sábado que ya pasó); cualquier otro día, el próximo sábado y domingo. Nueva pill "weekend" en `when-pills`, entre "Hoy" y "Esta semana" (el finde siempre cae dentro de la ventana de 7 días de "semana", por eso el orden). Nuevo string `whenWeekend` ES/FR.
- Verificado: `node --check` en `app.js`/`i18n.js`, balance de llaves en `style.css` (114/114), un test de Node que enumera `isWeekendEvent` para los 7 días de la semana posibles como "hoy" y confirma que los offsets marcados caen siempre en el sábado/domingo correcto (incluyendo el caso borde de "hoy es domingo"), y otro que ejercita `matchesSearch()` con tildes, mayúsculas, coincidencia parcial de cuenta/tag, y un caso sin match. **No visto en un navegador real.**

---

**DD-066 — Etapa 1: fusión de sinónimos de cityName (2026-08-27).** Diego dio luz verde para seguir con el plan de 4 etapas de DD-065 ("procede por favor"). Auditando los 100 valores distintos de `cityName` en `site/data.json` (751 eventos) para decidir la limpieza: 396 eventos (53%) sin `cityName`, pero solo 63 (8%) muestran la línea de ubicación realmente vacía en la tarjeta (el resto tiene `locationName` o `exactAddress` como respaldo, ya existente antes de este DD) — el problema visible hoy es más chico de lo que sugería el conteo crudo. El hallazgo que sí importa para más adelante: **309 eventos (41%) no tienen NI `geoZone` NI `cityName`** — ningún nivel geográfico. Para la Etapa 3 (menú país/ciudad tipo Meetup) eso sigue siendo un bloqueo real; para esta entrega (Etapa 1, solo limpieza) no se tocó — queda documentado para cuando se retome Etapa 3.

- **Sinónimos fusionados (mismo patrón que `GEO_ZONE_SYNONYMS`/`canonicalizeGeoZone` de DD-056, ahora `CITY_SYNONYMS`/`canonicalizeCityName`, aplicado una vez en `init()`):** Paris/París (166 eventos combinados, el caso grande), Marseille/Marsella (8), Boulogne-Billancourt/"Boulogne Billancourt" (7), Montreal/Montréal (2), Ciudad de México/Mexico City (2), Cartagena/"Cartagena de Indias" (6), Venice/Venecia (3). De paso, un bug de datos separado: una entrada tenía el string literal `"null"` en vez de estar vacía de verdad — ahora se normaliza a `null` real. Canónico elegido: ortografía francesa para ciudades de Francia (coherente con el país del proyecto y con SEO en francés a futuro); para el resto, la forma que ya predominaba en el dato real. **Deliberadamente NO se tocaron** entradas de un solo evento donde no había certeza de que fueran de verdad la misma ciudad (ej. "El Retiro", que es un municipio real de Colombia, no un error) — mismo criterio conservador que ya usó DD-056 para geoZone.
- **`cityLabel(city)` nueva:** traduce a español solo Paris→París y Marseille→Marsella (las únicas dos con volumen suficiente para que la diferencia se note) cuando `CURRENT_LANG === "es"` — mismo patrón que `geoLabel()`/`categoryLabel()`, no pisa el valor canónico. Aplicada en los tres lugares donde `cityName` se muestra como texto (tarjeta, hero, panel de detalle). **Deliberadamente NO aplicada** en `mapEmbedHtml()`/`directionsUrl()`/`gcalUrl()` — esas arman queries para Google Maps/Calendar, más confiable pasarles siempre la ortografía canónica/francesa que la traducida.
- **Buscador (DD-065):** el haystack de `matchesSearch()` ahora también incluye `CITY_LABEL_ES[ev.cityName]` directo (no `cityLabel()`, que solo devuelve la variante ES cuando el sitio está en modo ES) — así "marsella" encuentra eventos en Marseille sin importar en qué idioma esté el sitio en ese momento. "parís"/"paris" ya funcionaban de antes gracias al strip de acentos de `normalizeSearch()` (DD-065); Marseille/Marsella son palabras distintas, no solo un acento, por eso necesitaba esta entrada aparte.
- **Nota igual que DD-056 dejó para geoZone:** esto es un parche defensivo en el frontend, no una limpieza de la fuente (Neo4j / planilla). Si aparecen más variantes de ciudad a futuro, van a necesitar una entrada nueva en `CITY_SYNONYMS` o, mejor, una limpieza en origen — fuera de alcance de esta entrega.
- Verificado: `node --check` en `app.js`, y un test de Node que ejercita `canonicalizeCityName()`/`cityLabel()` contra los 8 pares de sinónimos + el bug de `"null"` + una ciudad sin sinónimo (Bogotá, debe quedar intacta) + el caso FR (Paris debe quedarse "Paris", sin tilde, en modo francés). **No visto en un navegador real.**

---

**DD-067 — Etapa 2: páginas estáticas por categoría con JSON-LD, para SEO de IA real (2026-08-27).** Diego confirmó seguir con el plan (después de pushear DD-060 a DD-066, y de decidir posponer la limpieza a fondo del origen de los datos geográficos, aceptando "sacrificar eventos" mientras tanto). Nuevo script `6_generate_seo_pages.py`, sin dependencia de Neo4j — lee `site/data.json` directo, mismo espíritu que el resto de scripts client-side del proyecto.

- **Por qué hacía falta ir más allá de URLs bonitas:** confirmado por búsqueda (2026-08-27, ver conversación) que a mediados de 2026 GPTBot/ClaudeBot/PerplexityBot no ejecutan JavaScript, solo leen el HTML que devuelve el servidor en la primera respuesta. El sitio interactivo es 100% renderizado en cliente — cualquier URL, por prolija que sea, les mostraría el mismo esqueleto vacío sin esto.
- **Alcance: solo por categoría, no por país/ciudad todavía.** `category` (11 valores fijos, 100% poblado, DD-066) es la única taxonomía lo bastante sólida hoy; geo sigue con 41% de eventos sin ningún dato (DD-066), y esa limpieza quedó pospuesta a pedido explícito de Diego. Páginas país/ciudad esperan a Etapa 3, después de esa limpieza.
- **Qué genera cada corrida (`python 6_generate_seo_pages.py`, `--dry-run` disponible, `--min-events` default 3):**
  - `site/categoria/<slug>/index.html` por cada categoría con al menos `--min-events` eventos PRÓXIMOS (no todos los históricos) — HTML semántico real (nada de innerHTML vía JS), con fecha/título/lugar/precio/descripción/link a la publicación original de cada evento, más un bloque `<script type="application/ld+json">` con un `@graph` de objetos `Event` (schema.org) — uno por evento, con `name`/`startDate`/`location`/`url` (deep-link `?evento=<id>`, mismo mecanismo de DD-063) y, condicionalmente, `description`/`image`/`isAccessibleForFree`/`organizer`. Corrida real (2026-08-27): 10 de 11 categorías superan el mínimo (144 eventos próximos con categoría sobre 751 totales) — "político" quedó sin página (0 eventos próximos), el "sacrificio" que Diego ya había aceptado.
  - `site/categoria/index.html` — hub que linkea a cada categoría con su conteo.
  - `site/sitemap.xml` — home + hub + cada página de categoría generada.
  - **Actualiza el footer de `site/index.html`** (bloque marcado con comentarios `SEO_CATEGORY_LINKS_START`/`_END`, no editar a mano) con `<a href>` reales a cada página de categoría. Esto es necesario, no cosmético: un crawler que no ejecuta JS jamás iba a descubrir `/categoria/gastronomico/` si el único lugar que la mencionaba era algo armado por `app.js` — con este bloque, ya hay un link real en el HTML crudo del home.
- **`site/robots.txt` (nuevo, estático, no lo toca el script):** `Allow: /` + referencia a `sitemap.xml`. Se evaluó agregar también un `llms.txt` (propuesta emergente de 2026) pero la misma búsqueda que confirmó lo de los crawlers también encontró que Google no le da ningún trato especial y un análisis independiente no encontró relación medible con mejores citas de motores de IA — no se agregó, decisión basada en evidencia y no en moda.
- **Bug encontrado y corregido antes de terminar:** la primera corrida mostraba "Marsella"/"París" sin fusionar en el `location` del JSON-LD, pese a DD-066 — porque `event_location_text()` lee `cityName` crudo de `data.json`, y la fusión de sinónimos de DD-066 vivía solo del lado del cliente (`app.js`, en memoria). Se duplicó el mismo diccionario `CITY_SYNONYMS` acá (mismo motivo que `CATEGORY_META`, Python y JS no comparten build step) y se aplicó en `event_location_text()`. **Nota, no resuelta:** esto solo fusiona el campo `cityName` — si el nombre de una ciudad aparece escrito adentro de `locationName`/`exactAddress` (pasa en algunos eventos, ej. cuando no hay venue específico y se usó el nombre de la ciudad como "ubicación"), sigue sin fusionar, ahí ni la fusión de DD-066 ni esta tocan nada — mismo límite que ya tiene el sitio interactivo, no es una regresión nueva de esta entrega.
- Verificado: `py_compile`, corrida real (no solo `--dry-run`) contra `site/data.json` real, `json.loads()` sobre el JSON-LD de las 11 páginas generadas confirma que parsea sin error, `html.parser.HTMLParser` sobre las 11 páginas sin levantar errores de parseo, grep confirmando que no quedó ningún `None` de Python filtrado al HTML por un campo faltante mal manejado. **No visto en un navegador real ni verificado con el validador de datos estructurados de Google/Bing** — pendiente que Diego lo confirme después de desplegar (Search Console / Rich Results Test una vez el dominio esté indexado).
- **Pendiente para Diego:** correr `python 6_generate_seo_pages.py` de nuevo cada vez que cambie `site/data.json` (después de `5_export_dashboard_data.py`, antes de `wrangler deploy`) — no está (todavía) encadenado automáticamente al resto de la pipeline.

---

**DD-068 — "Otras categorías": página temporal para categorías bajo el mínimo, en vez de excluirlas sin más (2026-08-27).** A raíz del reporte de DD-067 ("político" sin página por tener 0 eventos próximos), Diego pidió agrupar las categorías que no llegan al mínimo en un "Otros" — explícitamente temporal, mientras acumulan suficientes eventos.

- **"Temporal" es literal, no solo de palabra: no hay ningún estado guardado.** Cada corrida de `6_generate_seo_pages.py` recalcula todo desde cero a partir de los conteos actuales de `site/data.json` — en cuanto una categoría junte sola `--min-events` eventos en una corrida futura, esa misma corrida ya le arma su propia página y deja de aportarle eventos a "Otros". No hace falta ninguna acción manual de Diego para "graduar" una categoría de Otros a página propia.
- **Mismo criterio de contenido-no-delgado que las páginas normales, aplicado también a Otros:** si el total agrupado tampoco llega al mínimo, tampoco se genera la página de Otros — no tiene sentido mudar el problema de "contenido vacío" de una categoría a otra. Confirmado con la corrida real (min-events=3): "político" con 0 eventos es la única bajo el mínimo hoy, así que Otros también queda en 0 y no se genera — mismo resultado visible que antes de este cambio. El mecanismo en sí se probó forzando `--min-events 15` (dry-run): 6 categorías caen bajo ese umbral más alto, se agrupan en una página "Otros" de 47 eventos combinados, confirmando que la lógica funciona antes de que haga falta en producción.
- **La página de Otros, cuando exista, incluye una nota explícita** ("estos eventos son de categorías que todavía no juntan suficientes eventos... en cuanto la tengan, se muestran ahí en vez de acá") — tanto para el visitante humano como para dejar claro en el propio HTML (crawler-legible) que es un catch-all temporal, no una categoría real de la taxonomía.
- **Nota de continuidad, no aplica acá:** el sitio interactivo probó una idea parecida para el menú de filtros ("Otras categorías" para tags libres de 1-2 eventos, DD-055) y la sacó al toque (DD-056, "seguía siendo ruido visual"). Distinto caso: eso era sobre 50+ tags libres en un menú que Diego navega a diario; esto es sobre, como mucho, unas pocas de las 11 categorías fijas, en una página de aterrizaje para crawlers, no en un menú de uso diario — la objeción de "ruido visual" no se traslada directo.
- Verificado: `py_compile`, corrida real con `--min-events 3` (producción, mismo resultado que DD-067 ya que sigue sin haber suficientes eventos agrupables), corrida `--dry-run --min-events 15` forzando que "Otros" tenga contenido real para probar el camino completo, `json.loads()` + `HTMLParser` sobre las páginas generadas sin errores.

---

**DD-069 — Fallback de `location` hasta "Francia": corrige error crítico encontrado con el Rich Results Test de Google (2026-08-27).** Diego desplegó DD-067/068 y pasó `/categoria/visual/` por el [Rich Results Test](https://search.google.com/test/rich-results) de Google. Resultado, en general muy bueno: Google parseó los 32 eventos de esa página sin problemas de sintaxis — confirma que el JSON-LD generado es válido y legible desde afuera. Pero marcó 6 de esos 32 como error crítico: campo `location` ausente (obligatorio para que un `Event` sea elegible como resultado enriquecido; el resto de los campos que faltaban -- `offers`/`image`/`endDate`/`performer` -- son opcionales, sin problema). Esos 6 eran exactamente la cara visible de los 309 eventos (41%) sin `geoZone` ni `cityName` que ya había encontrado DD-066.

- **Fix: `event_location_text()` ahora tiene una cadena de fallback completa** — `exactAddress` → `locationName` → `cityName` (canonicalizado) → `geoZone` → `"Francia"` a secas como último recurso, en vez de devolver `""` cuando no hay ningún dato. "Francia" es verdadero (todos los eventos del catálogo son ahí) y no inventa precisión que no existe — es distinto de la limpieza de datos que Diego pidió postergar, esto es un valor de reserva del generador, no una corrección del dato de origen. Al ser una única función usada tanto por `event_html()` (texto visible) como por `event_jsonld()` (`location.name`), ambos quedan consistentes automáticamente.
- Verificado: corrida real regenerando las 10 páginas, y un chequeo que recorre el JSON-LD de las 144 eventos publicados confirmando 0 sin `location` (antes del fix, 6 solo en la categoría "visual" que Diego probó — probablemente más contando el resto de las categorías, no se midió el total exacto antes del fix). **Pendiente:** que Diego vuelva a correr el Rich Results Test sobre `/categoria/visual/` después de este deploy para confirmar que ya no marca ningún error crítico.

---

**DD-070 — Plan de limpieza del origen de datos geográficos (Etapa 3, pendiente) + Pieza A implementada (2026-08-28).** Diego preguntó qué implicaría concretamente mejorar el origen de los datos de `geoZone`/`cityName` (postergado en DD-066), y cuánto mejoró la última tanda de eventos extraídos. Análisis contra `site/data.json` real (751 eventos, sin acceso a Neo4j desde este sandbox):

- **La tendencia ya es buena:** ordenando por `sourcePostDate` (proxy disponible, no hay `firstSeenAt`/fecha de creación exportada a nivel `:Event`), los últimos 50 posts tienen 82% de cobertura de `geoZone`/`cityName`, los últimos 200 tienen 91% — contra 59% del catálogo completo. La curación manual que ya se viene haciendo está funcionando; el hueco grande es sobre todo del catálogo viejo.
- **Descomposición de los 309 eventos sin ningún dato geográfico**, en piezas de esfuerzo creciente:
  - **Pieza A (40 eventos):** la cuenta YA tiene `geoZone` curado en la planilla, pero el evento es de antes de esa curación (herencia cuenta→evento es solo-al-crear, nunca retroactiva — misma limitación que `photoPermission`/`artType`/etc., ver CLAUDE.md). Implementada en esta misma entrega, ver abajo.
  - **Pieza B (142 eventos, con 26 de superposición con la Pieza A):** tienen dirección/lugar en texto (`exactAddress`/`locationName`) pero nunca se geocodificaron (`lat` ausente). No necesita código nuevo — `4_enrich_locations.py` ya es idempotente (salta lo que ya tiene `lat`), alcanza con volver a correrlo.
  - **Pieza C (153 eventos restantes):** ni cuenta curada con `geoZone` ni texto de ubicación en el caption — necesitan trabajo real: terminar de curar las 64 cuentas (de 349) que todavía no tienen `geoZone` en la planilla, y/o aceptar que algunos captions genuinamente no mencionan un lugar.
  - **Pieza D (no implementada, evaluar después):** ni siquiera con A+B+C se llega a nivel de ciudad específica en todos los casos (`geoZone` es zona gruesa: Île-de-France / fuera de IDF / fuera de Francia) — si hace falta más cobertura de ciudad real (para el menú tipo Meetup que Diego quiere en la Etapa 3 propiamente dicha), la opción evaluada es agregar una columna nueva a la planilla ("ciudad principal de la cuenta") con el mismo patrón de herencia creation-only que ya usa `photoPermission`. Diego no pidió esto todavía.
- **`backfill_geo_zone.py` (nuevo, Pieza A):** mismo patrón que `backfill_event_images.py` — `MATCH (e:Event) MATCH (a:Account {username: e.sourceAuthor}) WHERE e.geoZone ausente AND a.geoZone presente SET e.geoZone = a.geoZone`, el mismo join que ya usa `5_export_dashboard_data.py`. Sin LLM, sin tocar la planilla, idempotente, `--dry-run` disponible. Agregado también al panel de control (`control_panel.py`, fase "5. Eventos") para que Diego pueda correrlo desde ahí.
- Verificado: `py_compile`, y un test con `neo4j`/`dotenv` stubeados que confirma que Typer introspecciona bien el único parámetro (`--dry-run`) — mismo mecanismo que usa el panel de control para descubrir argumentos. **No corrido contra Neo4j real** (sin acceso de red desde este sandbox, limitación de siempre) — el conteo de "40 eventos" es una proyección desde `site/data.json` (que puede no reflejar el estado más reciente de Neo4j si hubo cambios sin exportar), no el resultado real de la query; Diego tiene que correr `--dry-run` primero para confirmar el número exacto contra Neo4j antes de aplicar.

---

**DD-071 — Retención de eventos: corte de 30 días en el export, sin borrar nada en Neo4j (2026-08-28).** Diego preguntó si existía algún mecanismo que cerrara el ciclo de vida de un evento una vez pasada su fecha — no existía ninguno: `EVENTS_QUERY` de `5_export_dashboard_data.py` nunca filtró por antigüedad (solo excluye `:Rejected`/`:PendingReview`), y el bucket "Pasados" de `app.js` (`whenBucket`) mostraba literalmente todo lo anterior a hoy sin ningún tope — el evento más viejo en `data.json` es de 2018. Diego también planteó que, dado esto, las Piezas C/D del plan de limpieza geográfica de DD-070 (curar más cuentas, agregar columna de ciudad) le interesaban poco porque sospechaba que la mayoría de esos 309 eventos sin geo eran justamente eventos viejos que de todas formas piensa sacar del sitio.

- **Verificación de la hipótesis de Diego, contra `site/data.json` real:** de los 309 eventos sin `geoZone` ni `cityName`, solo 23 son próximos (los otros 286 son pasados o sin fecha parseable) — y de esos 23, la Pieza A (`backfill_geo_zone.py`) solo rescataría 1 y la Pieza B (re-geocodificar) solo rescataría 5. Quedan 17 eventos próximos genuinamente sin señal geográfica, repartidos en 13 cuentas puntuales (`alejam09`, `alianzafrancesamanizalesoficia`, `athenee.theatre`, `cg_brasil_paris`, `eecufrancia`, `la_fab_officiel`, `lareguliere`, `latitud4podcast`, `lecarreaudutemple`, `librairies_charlemagne`, `paris.avec.accent`, `prosantamartav`, `ueencolombia`) — no las 64 cuentas sin `geoZone` en general que planteaba la Pieza C original de DD-070. Confirma la intuición de Diego: el problema geográfico accionable sobre el catálogo vivo es mucho más chico de lo que sugería el conteo de 309.
- **Escala del problema de fondo:** de 743 eventos con fecha parseable, 530 (71%) ya están a más de 30 días de hoy, 266 (36%) a más de 90 días, 85 (11%) a más de un año — ese es el peso muerto que hoy se manda completo a cada visitante vía `data.json`, sin que nada lo recorte nunca.
- **Arquitectura elegida — tres capas, respetando la decisión ya tomada de nunca borrar un `:Event` (ver `review_events.py`, "rechazar" es soft-tag, nunca `DETACH DELETE`):**
  1. **Neo4j — archivo permanente, sin cambios.** No se borra ningún nodo; conserva valor histórico para la tesis y no tiene costo real de almacenamiento a esta escala.
  2. **`5_export_dashboard_data.py` — el filtro real.** Nueva constante `PAST_RETENTION_DAYS = 30` y opción `--past-days` (mismo default); `EVENTS_QUERY` gana `AND substring(e.eventDate, 0, 10) >= $cutoffDate`, comparación de string sobre los primeros 10 caracteres de `eventDate` — mismo patrón ya usado en `4_enrich_events_extract.py` para `--max-post-age-days` (DD-048), válido porque fechas ISO ordenan lexicográficamente igual que cronológicamente. Los eventos futuros siempre pasan el filtro (`>=` cutoff). `export_events_excel.py` queda intacto a propósito — sigue siendo el archivo completo de uso interno de Diego, no algo que se sirve al público.
  3. **`app.js` — refuerzo en cliente.** Misma constante `PAST_RETENTION_DAYS = 30` duplicada (mismo motivo de siempre: Python y JS no comparten build step, ver DD-067 con `CITY_SYNONYMS`); el bucket `STATE.when === "past"` de `applyFilters()` ahora excluye eventos a más de 30 días, doble capa aunque el JSON ya venga recortado, porque el pedido explícito de Diego ("en pasados solo lo reciente") es una regla de producto, no solo un efecto colateral del recorte del export.
- **Qué hacen sitios como Meetup (contexto para la decisión, no investigado puntualmente — conocimiento general estable del dominio):** conservan el historial completo a nivel de organización/grupo, accesible aparte (pestaña de eventos pasados, paginada), pero las superficies de descubrimiento (home, búsqueda, categorías) solo cargan próximos + pasado reciente — nunca mandan el catálogo entero en un solo payload. Mismo patrón de las tres capas de arriba: Neo4j como archivo completo, `data.json` como vista liviana.
- **Impacto medido (simulado sobre el `data.json` actual, sin acceso a Neo4j desde este sandbox):** de 751 eventos, quedarían 213 bajo el nuevo corte (538 se recortan) — el peso del array `events[]` bajaría de ~1101 KB a ~381 KB, una reducción de ~65%.
- **Continuidad con Etapa 3 (URLs de carpeta):** no se abandona, ver cierre explícito de Diego ("sin dejar de lado nuestra etapa 3"). Este cambio la simplifica — con ~200 eventos vivos en vez de 751, los umbrales de "mínimo de eventos" por país/ciudad son más fáciles de calibrar. `6_generate_seo_pages.py` (Etapa 2, DD-067) ya filtraba solo por próximos, así que no le afecta este cambio.
- **Piezas C/D de DD-070 — reencuadradas, no descartadas:** dado que solo 17 eventos próximos quedan sin geo después de A+B, una Pieza C reducida a esas 13 cuentas puntuales sigue teniendo sentido (barata, ya identificadas); la versión amplia (64 cuentas) y la Pieza D (columna nueva de ciudad) quedan de baja prioridad, a criterio de Diego.
- Verificado: `py_compile` en `5_export_dashboard_data.py`, `node --check` en `app.js`, y una simulación en Python del filtro (`eventDate[:10] >= cutoff`) contra `site/data.json` real confirmando el recorte 751→213 y el ahorro de peso. **No corrido el export real contra Neo4j** (sin acceso de red desde este sandbox, limitación de siempre) — Diego tiene que correr `python 5_export_dashboard_data.py` (con o sin `--past-days` si quiere otro valor) desde su máquina para regenerar `site/data.json` de verdad, y después `python 6_generate_seo_pages.py` + `git push` + `wrangler deploy` para que el recorte llegue al sitio en vivo.

---

**DD-072 — Etapa 3: páginas de geo por zona dentro de Francia, más deep-link de filtros (2026-08-29).** Diego corrió `4_enrich_locations.py` real: 0/276 Location geocodificadas (el bloque final que sí falló, `SessionExpired`, es Neo4j Aura cortando la conexión después de los ~13 minutos que duró la corrida — un problema de conectividad aparte, no la causa del 0/276). Diagnóstico no confirmado — el script ya manda un `user_agent` propio (descarta la causa más común de bloqueo de Nominatim), así que podría ser bloqueo de IP/red del lado de Nominatim, una política más estricta reciente, o el mismo tipo de interferencia de red que ya afecta al puerto 7687 de Neo4j en este entorno; sin ver la respuesta HTTP real no se puede afirmar cuál. Diego decidió no perseguir esto ni curar las 13 cuentas identificadas en DD-071 para este fin, y pidió armar el plan completo de Etapa 3 (URLs de carpeta país/ciudad, pendiente desde DD-065).

- **Por qué el fallo de Nominatim NO bloquea Etapa 3:** el menú país/ciudad se construye sobre `geoZone`/`cityName` del propio `:Event` (extraídos por el LLM al crear el evento), no sobre la jerarquía `:City`/`:Country` que arma `4_enrich_locations.py` — esa jerarquía solo decide si el evento tiene pin de mapa (DD-045, "aparecer en el sitio y tener pin en el mapa son dos cosas independientes"), nunca si aparece en una página de geo.
- **Los datos reales no calzan con la idea original de un menú tipo Meetup con muchas ciudades elegibles.** Contra el catálogo vivo (213 eventos, ya con el corte de DD-071): Île-de-France 112, Francia fuera de IDF 23, Fuera de Francia 16 (dispersos: Madrid 4, Berlín/Houston/Londres 1 cada uno), sin geoZone 54, no confirmado 8. A nivel ciudad, después de fusionar sinónimos (DD-066), solo Paris tiene volumen real (66) — el resto son 1-4 eventos por ciudad (Marseille 4, Madrid 4, Montpellier 3, Toulouse 2...). Un menú con una entrada por ciudad hubiera sido, en la práctica, una lista de ciudades con un evento cada una.
- **Alcance elegido para v1, confirmado con Diego vía preguntas explícitas:** dos páginas de zona (`/francia/ile-de-france/`, `/francia/fuera-de-ile-de-france/`) más una subpágina anidada solo para Paris (`/francia/ile-de-france/paris/`, único caso con volumen a nivel ciudad) — nada a nivel país más allá de Francia por ahora. Los 16 eventos "Fuera de Francia" quedan **sin página en v1** (misma decisión que "político" en Etapa 2, DD-067) — Diego prefiere explícitamente NO forzar un pooling genérico ahí, y en su lugar dejar para una entrega futura un procedimiento que arme estructura propia por país cuando haga falta (ej. `/es/madrid/`, `/de/berlin/`).
- **`6_generate_seo_pages.py` extendido, no un script nuevo** — mismo espíritu de Etapa 2 (todo el SEO estático vive en un solo generador). Piezas nuevas: `GEO_ZONE_SYNONYMS`/`canonical_geo_zone()` (mismo par que `GEO_ZONE_SYNONYMS` de `site/app.js`, DD-056, duplicado por el mismo motivo de siempre — Python/JS sin build compartido), `FRANCIA_ZONES` (las dos zonas con página propia), y un refactor de `render_category_page()` hacia una función compartida `render_listing_page()` que ahora también usa `render_geo_page()` — mismo template (HTML semántico + JSON-LD `@graph`), solo cambian breadcrumb/título/link de vuelta. `render_francia_hub_page()` nueva (mismo patrón que el hub de categorías) — nunca incluye a Paris como tercer link de primer nivel, porque es subpágina de Île-de-France, no una zona hermana.
- **Deep-link de filtros, cerrando el círculo estático↔interactivo (pedido explícito de Diego, "en esta entrega"):** cada página estática ahora enlaza de vuelta al sitio interactivo YA FILTRADA, no a la home a secas — `?tema=<categoria>` en páginas de categoría (excepto "Otros", que agrupa varias categorías y no tiene un único tema que aplicarle al link — cae al link genérico sin filtro), `?geo=<zona>` en las páginas de zona, y `?buscar=Paris` en la página de Paris (reutiliza el buscador de Etapa 0/DD-065 en vez de inventar un filtro de ciudad nuevo en `STATE`, porque hoy no existe esa granularidad de filtro). `applyInitialFiltersFromUrl()` (`site/app.js`) lee estos params en `init()`, antes del primer render — muta `STATE` directo, sin pasar por `setState()` (para no contaminar `catWeights`/`bumpPref`, pensados para reflejar clicks genuinos del visitante durante la sesión, no de dónde vino el link de entrada) ni por `pushState` (es el estado inicial de la carga, no una navegación nueva). Mismo espíritu que `?evento=<id>` de DD-063, pero para una lista de eventos en vez de uno solo.
- **Footer de `index.html`:** nuevo bloque marcado `SEO_GEO_LINKS_START/END`, separado del bloque de categorías (`SEO_CATEGORY_LINKS`) para que cada uno se reescriba independiente sin pisar al otro — mismo argumento de DD-067 (un crawler sin JS no descubre `/francia/...` si el único lugar que lo menciona es algo armado por `app.js`). `sitemap.xml` incluye las 4 URLs nuevas.
- **Corrida real contra `site/data.json`** (eventos estrictamente próximos, sin el corte de 30 días de DD-071 que sí aplica el export pero no este generador — ver nota ya existente en el código): Île-de-France 67, Francia fuera de IDF 10, Paris (subconjunto de IDF) 30 — los tres superan el mínimo y generan página.
- Verificado: `py_compile`, `node --check` en `app.js`, un test de Node de `applyInitialFiltersFromUrl()` con 7 casos (cada param válido, params inválidos que no deben aplicar nada, sin params), corrida real (no dry-run) de `6_generate_seo_pages.py`, `HTMLParser` + `json.loads()` sobre las 4 páginas de geo nuevas confirmando 0 eventos sin `location` en el JSON-LD, y una inspección manual del breadcrumb/link de "ver en vivo" de la página de Paris y del footer de `index.html` confirmando que las 4 URLs de geo quedaron enlazadas. **No visto en un navegador real** ni el deep-link `?geo=`/`?tema=`/`?buscar=` probado en un navegador de verdad — falta que Diego lo confirme después de desplegar. **Pendiente, aparte:** diagnosticar el 0/276 de Nominatim si en algún momento vuelve a hacer falta geocodificar (mapas/pines), aunque no bloquea nada de esta entrega.

---

**DD-073 — Menú país→zona→ciudad en cascada + URLs bonitas /fr/... para la vista interactiva en vivo (2026-08-29).** Diego vio las páginas de geo de DD-072 y preguntó dos cosas: (1) confirmar que esas páginas (planas, sin la estética del sitio) son para SEO de IA — sí, correcto, mismo criterio que las de categoría (DD-067); (2) cuándo se integra el menú real donde uno elige país/ciudad y aterriza en una vista CON la estética del sitio, tipo `hubcultural.com/fr/` — esa pieza todavía no estaba hecha, solo el deep-link de query params (`?geo=`) que conectaba las páginas SEO con el filtro ya existente. Confirmado con Diego: URLs bonitas bajo `/fr/` (no reusar `/francia/`, que ya es la ruta de las páginas SEO planas) + arrancar ya con el rediseño del menú y el routing.

- **Menú en cascada (`site/app.js`):** nuevo `GEO_FILTERS`, diccionario de slugs con predicado propio en vez de comparar `ev.geoZone` directo contra `STATE.geo` (el diseño de DD-072) — necesario porque "francia" (IDF + fuera-de-IDF combinadas) y "paris" (geoZone + cityName combinados) no son valores de `geoZone` reales, son agregaciones que no existían como opción de filtro. `parent` en cada entrada arma la jerarquía; `geoIsActiveBranch()` resuelve si un nodo padre debe verse "activo" cuando el seleccionado es un descendiente (ej. parado en "paris", tanto "Francia" como "Île-de-France" se ven resaltados). `renderFilterBar()` ahora pinta 3 filas de pills en cascada (país: Francia/Fuera de Francia; zona: Île-de-France/Francia fuera de IDF, solo visible con Francia activo; ciudad: Paris, solo visible con Île-de-France activo) en vez de una lista plana armada dinámicamente desde los valores de `geoZone` presentes en los datos.
- **Por qué no un menú N-país/M-ciudad genérico:** con los datos reales (ver DD-072) el único país con volumen es Francia y la única ciudad con volumen es Paris — un árbol genérico habría quedado casi vacío en la práctica. Cuando haya datos de otros países (la estructura `/es/madrid/` que Diego mencionó en DD-072), este mismo patrón de `GEO_FILTERS` se extiende agregando entradas nuevas, no rediseñando nada.
- **URLs bonitas (`/fr/`, `/fr/ile-de-france/`, `/fr/ile-de-france/paris/`, `/fr/fuera-de-ile-de-france/`):** implementadas como copias LITERALES de `site/index.html` en esas rutas (mismo archivo, mismo bytes) — Cloudflare Workers Assets las sirve como archivos estáticos reales, sin necesitar configurar un SPA fallback en `wrangler.jsonc` (alternativa evaluada y descartada: un `not_found_handling: "single-page-application"` global serviría `index.html` para CUALQUIER ruta no encontrada, incluyendo typos, perdiendo los 404 reales — las copias explícitas son más chicas en superficie de riesgo). `site/index.html` ganó un `<base href="/">` en el `<head>` (antes de cualquier `href`/`src` relativo) porque sin eso, servido desde `/fr/ile-de-france/paris/index.html`, los `href`/`src` relativos de `style.css`/`app.js`/`i18n.js`/`config.js` y el `fetch("data.json")` de `app.js` se habrían resuelto contra esa ruta anidada en vez de la raíz del sitio, rompiendo la página. `6_generate_seo_pages.py` reescribe estas 4 copias en cada corrida (lee `site/index.html` una vez, lo escribe en las 4 rutas) — siempre las 4, sin el gating por `--min-events` que sí aplica a las páginas SEO planas, porque acá no hay riesgo de "contenido delgado" (es la misma app interactiva de siempre, que ya sabe mostrar "sin eventos" con gracia, y de todas formas es invisible para los crawlers sin JS que son la razón de ser del gating en las páginas SEO).
- **Routing por pathname (`applyInitialFiltersFromUrl()`):** nuevo `ROUTE_TO_GEO` (inverso de `GEO_TO_ROUTE`) — si `location.pathname` coincide con una ruta conocida, define `STATE.geo` directo, con prioridad sobre `?geo=` (que sigue funcionando como fallback, ahora aceptando las claves de `GEO_FILTERS` sin traducción intermedia, a diferencia del `GEO_SLUG_TO_ZONE` de DD-072 que quedó obsoleto y se retiró). Clickear un pill de geo con ruta propia empuja esa URL vía `history.pushState` (`setGeo()` en `renderFilterBar()`); el listener de `popstate` se extendió para revertir `STATE.geo` correctamente en atrás/adelante, con cuidado explícito de NO resetear a "all" cuando el pathname es "/" pero el geo actual nunca tuvo ruta propia (ej. "fuera-de-francia") — un popstate ajeno (como el de abrir/cerrar el panel de un evento) no debe pisar un filtro de geo que nunca cambió la URL.
- **Links "ver en vivo" de las páginas SEO de DD-072, actualizados:** ya no usan `?geo=`/`?buscar=Paris` sino las rutas bonitas reales (`/fr/ile-de-france/`, etc.) — mejora también de precisión para Paris específicamente, que antes dependía del buscador de texto (`?buscar=Paris`, aproximado) y ahora usa el predicado exacto `geoZone === "Île-de-France" && cityName === "Paris"` de `GEO_FILTERS`.
- **Corrida real:** 17 archivos escritos (10 categoría + 3 geo + 4 copias `/fr/...`), diff byte-a-byte confirmando que las 4 copias son idénticas a `site/index.html`, sitemap con las 4 URLs `/fr/...` sumadas a las de `/francia/...`.
- Verificado: `node --check`, balance de llaves en `style.css` (116/116), un test de Node con 11 casos de `GEO_FILTERS`/`geoMatches`/`geoIsActiveBranch` (cada combinación de zona/ciudad, incluyendo el caso "sin geo" y la cascada activa en cada nivel), `HTMLParser` sobre `index.html` y dos copias `/fr/...`, `diff` confirmando byte-a-byte que las copias son idénticas al original. **No visto en un navegador real** — en particular el `pushState`/`popstate` de geo (atrás/adelante del navegador cambiando el filtro visualmente) no se puede ejercer fuera de un navegador de verdad; falta que Diego lo confirme después de desplegar.

---

**Tesis:** `thesis/main.tex` — outline completo en LaTeX/inglés, revisado por un agente Opus (correcciones factuales sobre este mismo decision log, capítulos 3–5 completados con prosa real anclada en runs/decisiones reales, nueva §3.7 "Evaluation Design"; actualizado 2026-08-25/26 para incorporar DD-047 a DD-053 — cascada de 4 proveedores LLM, staging review humano, eventos bilingües, deploy manual de Cloudflare Workers). Gaps pendientes: sin `references.bib`, sin postura ética explícita sobre ToS de Instagram, sin figura del pipeline ni capturas del sitio, cifra de "73–83 comunidades" sin validar contra una corrida real antes de citarla, y todavía no incorpora DD-054 (traducción ES/FR de categorías/geo/fecha/tags).

---

*Última actualización: 2026-08-29 (tarde)*
*Próximas decisiones a documentar: DD-023 (clasificador NLP de cuentas), SetFit para v2, integración TikTok, human-in-the-loop para revisión de eventos, resolución de DD-035 (exposiciones en curso), normalización de dígitos Unicode estilizados si se confirma que es frecuente, validación de los fixes DD-038/DD-039 en corridas reales, y si la muestra de "conflicto geográfico" (DD-041) revela falsos positivos del gazetteer. También pendiente: limpieza de basura preexistente en eventos legacy (fecha `1492-11-01`, emoji como `locationName`, texto no-geográfico como `locationName`). Validación en vivo de DD-042 (`eventArtTags`) contra output real del LLM, y decisión sobre si vale la pena un backfill de los eventos existentes. Los 8 puntos de DD-045 son ahora el punchlist activo — arrancar por el 1 y el 2 (ya diagnosticados, sin trabajo de investigación adicional).*
