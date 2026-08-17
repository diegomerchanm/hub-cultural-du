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
5. Construir mapa clickeable (arrondissements + comunas de petite couronne)
6. Banco de ~50 imágenes genéricas por categoría (fotografía, teatro, etc.) — candidato a trabajo con Opus/design
7. Ayudar a seleccionar cuentas nuevas a partir del pool `discoveredViaCuratedAccount=true` / `candidateReviewStatus='pending'` (ver `cleanup_legacy_accounts.py`)
8. Traducción ES/FR del contenido de eventos — al final de la lista a propósito, como edición a la pipeline de extracción (nuevo `titleFr`/`descriptionFr` generado por el LLM en `4_enrich_events_extract.py` al crear el evento); aplica solo a eventos nuevos, no retroactivo sobre los 170 existentes

**Tesis:** `thesis/main.tex` — outline completo en LaTeX/inglés, revisado por un agente Opus (correcciones factuales sobre este mismo decision log, capítulos 3–5 completados con prosa real anclada en runs/decisiones reales, nueva §3.7 "Evaluation Design"). Gaps pendientes: sin `references.bib`, sin postura ética explícita sobre ToS de Instagram, sin figura del pipeline ni capturas del sitio, cifra de "73–83 comunidades" sin validar contra una corrida real antes de citarla.

---

*Última actualización: 2026-08-17*
*Próximas decisiones a documentar: DD-023 (clasificador NLP de cuentas), SetFit para v2, integración TikTok, human-in-the-loop para revisión de eventos, resolución de DD-035 (exposiciones en curso), normalización de dígitos Unicode estilizados si se confirma que es frecuente, validación de los fixes DD-038/DD-039 en corridas reales, y si la muestra de "conflicto geográfico" (DD-041) revela falsos positivos del gazetteer. También pendiente: limpieza de basura preexistente en eventos legacy (fecha `1492-11-01`, emoji como `locationName`, texto no-geográfico como `locationName`). Validación en vivo de DD-042 (`eventArtTags`) contra output real del LLM, y decisión sobre si vale la pena un backfill de los eventos existentes. Los 8 puntos de DD-045 son ahora el punchlist activo — arrancar por el 1 y el 2 (ya diagnosticados, sin trabajo de investigación adicional).*
