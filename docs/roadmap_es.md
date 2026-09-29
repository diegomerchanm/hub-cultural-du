# Hub Cultural — Roadmap v2 (después de la tesis)

*Creado: 26/09/2026 · Autor: Diego Merchán + Claude · Estado: borrador para validar*

## Checklist de progreso

*Convención: al final de cada intercambio, Claude dice en el chat en qué punto de este checklist estamos. Documentos solo cuando sirvan al proyecto; todo lo demás, en el chat.*

**Fase 0 — Taxonomía**
- [x] Borrador v0 (26/09)
- [x] Borrador v0.2 ampliado: 8 universos, ~45 temas, criterio de exclusión de lo masivo (26/09)
- [x] Zonas grises excluidas: vida nocturna, espiritualidad alternativa, cursos de pago, escape games (26/09)
- [ ] Diego valida la estructura final
- [ ] `config/taxonomia.json` v1 (palabras clave FR/NL/EN/ES, tags OSM, hashtags)
- [ ] Tabla de mapeo de las 11 categorías viejas validada

**Fase 1 — HikerAPI completo**
- [x] Cliente compartido `hikerapi_client.py` (tope de gasto, caché de user_id, reintentos, log) — DD-076
- [x] Perfiles vía `/v1/user/by/username` (`1_harvest_ig_profiles_hikerapi.py`)
- [x] Posts reescrito: ventana dinámica + fusión + tope 50
- [x] Apify a `old/`; control_panel, workflow y CLAUDE.md actualizados; test offline OK
- [ ] **Diego:** `calibrate` perfiles + `calibrate` posts + `compare` en 2-3 cuentas (desde su Windows)
- [ ] **Diego:** secreto `HIKERAPI_ACCESS_KEY` en GitHub; commit de los cambios
- [ ] Costo real por request confirmado y ajustado

**Fase 2 — Motor de descubrimiento**
- [ ] Prueba manual de los endpoints de búsqueda de HikerAPI (users, places, location medias, hashtags)
- [ ] `0_discover_accounts.py` (OSM → places → users → hashtags → búsqueda de eventos → expansión)
- [ ] Filtro de candidatos (reglas + embeddings + LLM) con evidencia
- [ ] Piloto teatro × París: recall, precisión y costo medidos

**Fase 3 — Revisión y catálogo**
- [ ] Pestaña de candidatos en Streamlit
- [ ] Seeds por ciudad/tema y propiedades nuevas en `:Account`

**Fase 4 — Pipeline alineada con la taxonomía**
- [ ] Capa 1 y Capa 3 (prompt sin sesgo colombiano, tema/subtema/formato/público)
- [ ] Migración de los 983 eventos
- [ ] Limpiezas (fechas aberrantes, geoZone)

**Fase 5 — Personalización en el sitio**
- [ ] Pantalla de bienvenida (ciudad + temáticas) y filtros nuevos
- [ ] Formulario "¿No ves tu temática?"

**Fase 6 — Mantenimiento**
- [ ] Re-descubrimiento periódico + detección de cuentas inactivas

**Fase 7 — Gante**
- [ ] `config/ciudades/gent.json`, neerlandés, rutas por ciudad
- [ ] Prueba del NLI en neerlandés
- [ ] Evaluación de la UiTdatabank

**Transversal**
- [ ] `.env` fuera de la carpeta + regla de secretos en CLAUDE.md
- [ ] DD-076+ en el decision log
- [ ] Worker huérfano de Cloudflare

## Punto de partida

La tesis está cerrada. El proyecto pasa de ser un estudio a ser una **herramienta**. La pipeline de eventos funciona: extracción en 3 capas, revisión humana en Streamlit, export estático y deploy en Cloudflare. Su cuello de botella es **de dónde salen las cuentas**: hoy son una lista fija, construida a mano cuenta por cuenta en Instagram. Eso es lento, desgastante y no escala a más temáticas ni a más ciudades.

## Objetivos

1. **Cuentas personalizables por temática.** Un catálogo de cuentas organizado por temática × ciudad. Se construye con un motor de descubrimiento semiautomático y se actualiza periódicamente. Para el visitante, la personalización es un **filtro instantáneo** sobre ese catálogo, no un proceso en tiempo real.
2. **Multi-ciudad.** La misma pipeline para otra ciudad. La segunda ciudad es **Gante (Gent, Bélgica)**.

## Decisiones ya tomadas (26/09/2026)

- **Nada de on demand en tiempo real.** Las cuentas se acumulan por temática y se refrescan de vez en cuando. El usuario solo filtra.
- **Migración completa a HikerAPI**, dejando Apify.
- **Fuentes de descubrimiento aprobadas:** OpenStreetMap (lugares), búsqueda por palabras clave en el nombre de la cuenta, búsqueda de eventos con ventana temporal y expansión desde las mejores cuentas.
- **La taxonomía va primero**, y debe ser muy amplia: todo lo que una persona puede hacer un fin de semana. Cultura, ideas y ciencia (filosofía, sociología, feminismo, clima, física, geología), baile con sus subtipos, deporte participativo, aire libre, planes sociales. **Se excluye lo masivo**, que ya tiene su público: fútbol y deportes-espectáculo, conciertos de estadio, franquicias.
- **Meta de volumen:** entre 30 y 50 cuentas organizadoras por temática y ciudad. Hipótesis: rara vez hay más de unas 50 por ciudad.

---

## Fase 0 — Taxonomía (EN CURSO)

**Por qué primero:** alimenta todo lo demás. Define qué se busca (palabras clave, tags OSM, hashtags), cómo el LLM clasifica los eventos y qué filtros ve el visitante.

**Cambio estructural propuesto:** hoy las 11 categorías mezclan tres cosas distintas:

- el **tema**: música, cine;
- el **formato**: taller, festival, charla;
- el **organizador**: institucional, comunitario.

Un "taller de salsa" o un "festival de cine" no caben bien en una sola casilla. La propuesta es separar tres dimensiones: **Tema** (con subtemas), **Formato** y **Público**. Ver `docs/taxonomia_v0_es.md`.

**Entregables:**

- `docs/taxonomia_v0_es.md`: borrador para discutir.
- `config/taxonomia.json` (v1): la fuente única que leen los scripts y el sitio. Tiene temas, subtemas, formatos y públicos; etiquetas en ES/FR/NL/EN; palabras clave por idioma; tags OSM; hashtags semilla.
- Tabla de mapeo de las 11 categorías actuales a la taxonomía nueva, para no perder los 983 eventos existentes.

**Listo cuando:** Diego valida la v1 y cada subtema tiene al menos una fuente de descubrimiento definida (OSM, palabras clave o hashtags).

## Fase 1 — HikerAPI completo

- Correr `calibrate` y `compare` de `1_harvest_ig_posts_hikerapi.py`, pendientes desde DD-059. El objetivo es confirmar la forma real de la respuesta y el costo por cuenta.
- Reemplazar también el scraper de perfiles: `/v1/user/by/username` ya da bio, seguidores y categoría.
- Pasar los scripts de Apify a `old/`, actualizar `CLAUDE.md`, `requirements.txt` y el workflow de GitHub Actions.
- Registrar el costo real por request en `.hikerapi_cost_log.json`. La referencia actual, ~0,0006 USD por request, es un supuesto que hay que verificar.

**Listo cuando:** una corrida completa de posts para las cuentas actuales sale 100 % por HikerAPI y `2_build_graph.py` la ingiere sin cambios.

## Fase 2 — Motor de descubrimiento de cuentas

Script nuevo: `0_discover_accounts.py --tema <slug> --ciudad <slug>`. Va de la fuente más barata y precisa a la más cara:

1. **OpenStreetMap (Overpass, gratis):** lugares de la ciudad según los tags OSM del subtema (`amenity=theatre`, `amenity=cinema`, `shop=books`, `sport=climbing`…). Algunos ya traen `contact:instagram` o sitio web.
2. **Lugar a Instagram:** buscar cada lugar OSM con `/v1/fbsearch/places` (nombre + lat/lng) para obtener el `location_pk`. Luego `/v1/location/medias/recent` da quién publica desde ese lugar: la cuenta del lugar, organizadores, compañías.
3. **Búsqueda por nombre de cuenta:** `/v1/search/users?query=<palabra clave> <ciudad>`, con las palabras clave localizadas de la taxonomía (librairie, boekhandel, théâtre, schouwburg…). Es el hallazgo de agosto: ~90 % de las cuentas buenas se reconocen por el nombre.
4. **Hashtags:** `/v1/hashtag/medias/top/chunk` con los hashtags semilla (#salsaparis, #gentstheater…).
5. **Búsqueda de eventos con ventana temporal:** consultas generadas automáticamente sobre las próximas 2 a 6 semanas, con nombre del mes ("octobre 2026"), fines de semana concretos ("samedi 10 octobre") y fechas clave ("Nuit Blanche"). Sirve para encontrar cuentas activas ahora. Requiere un buscador con API (Brave Search o el scraper de Google de Apify). Ojo: en esta cuenta de Claude la búsqueda web está desactivada por el administrador, así que esta fuente tiene que vivir en el script, no en el chat.
6. **Expansión acotada:** `following` de las 5 mejores cuentas validadas del tema. Nunca de todas, para no repetir los ~7 900 candidatos de agosto.

**Filtro de candidatos:**

- Deduplicación.
- Reglas duras: cuenta pública, activa en los últimos 60 días, no es persona individual.
- Clasificador por embeddings (DD-046) + LLM que lee bio y últimos posts y responde: ¿organiza eventos? ¿está en la ciudad? ¿qué subtemas?
- Cada candidato sale con **evidencia**, es decir, de qué fuente vino y por qué pasó, y con un score.

**Salida:** `data_processed/candidatos/<ciudad>/<tema>.csv`.

**Guardarraíles:** `--dry-run` que estima el costo antes de gastar, tope de gasto por corrida (`--max-usd`), log de cada llamada y caché para no volver a pagar por la misma consulta.

**Piloto medible: teatro × París.** Diego ya tiene cuentas de teatro curadas, así que se puede medir:

- **recall:** qué % de las cuentas buenas conocidas encuentra el motor;
- **precisión:** qué % de lo que propone sirve;
- **costo:** USD por cuenta válida.

**Listo cuando:** el piloto da al menos 30 cuentas válidas para teatro-París, con un recall razonable sobre las conocidas y un costo por cuenta aceptable. Los umbrales exactos se fijan con Diego antes del piloto.

## Fase 3 — Revisión de candidatos y catálogo

- Pestaña nueva en `review_events.py` (Streamlit): lista de candidatos con foto, bio, evidencia y score, y botones Aprobar / Rechazar / "Otro tema". Se decide en segundos por cuenta, sin investigar en Instagram.
- Las cuentas aprobadas se guardan en `config/seeds/<ciudad>/<tema>.json` y en `:Account` con propiedades nuevas: `cities`, `themes`, `subthemes`, `discoveredVia`, `approvedAt`. Una cuenta puede tener varios temas.
- Las listas de exclusión (`excluded_accounts.json`) pasan a ser por ciudad.
- La curación manual (Excel v4) sigue válida como capa extra, pero ya no es obligatoria para entrar al catálogo.

## Fase 4 — Pipeline de eventos alineada con la taxonomía

- **Capa 1:** frases de referencia generadas desde la taxonomía, no escritas a mano.
- **Capa 3 (LLM):** reescribir el prompt y `_LABEL_META`. Hoy tienen sesgo colombiano ("cultura colombiana", "consulado colombiano"). El LLM devuelve tema, subtema, formato y público según la v1.
- **Export y sitio:** `data.json` con los campos nuevos; filtros por tema (con subtemas), formato y público; páginas SEO por tema.
- Migrar los eventos existentes con la tabla de mapeo de la Fase 0.
- Limpiezas pendientes del export actual:
  - fechas aberrantes (2035, 2039, 2060);
  - sinónimos de geoZone;
  - 55 % de eventos sin geoZone.

## Fase 5 — Personalización en el sitio

- **Primera visita:** pantalla de bienvenida donde la persona elige ciudad y temáticas. Se guarda en su navegador y puede cambiarla cuando quiera.
- El home se ordena y filtra según esas preferencias. Es instantáneo porque solo se filtra `data.json`.
- **Formulario "¿No ves tu temática?":** la petición queda en cola, la revisa Diego y alimenta la Fase 2. Nunca dispara gasto automático.

## Fase 6 — Mantenimiento del catálogo

- Re-descubrimiento periódico, por ejemplo mensual por tema y ciudad, para encontrar cuentas nuevas.
- Detección de cuentas inactivas (sin posts en 90 días), que se marcan pero no se borran.
- Tarea programada que prepara los candidatos. Diego solo aprueba.

## Fase 7 — Multi-ciudad: Gante

- `config/ciudades/<ciudad>.json`: nombre, país, idiomas (Gent: NL principal; FR/EN secundarios), bbox para OSM, zonas o barrios, zona horaria.
- Sacar del código todo lo que está atado a París o Francia: `GEO_FILTERS`, zonas geo, rutas `/fr/…`. Nuevas rutas por ciudad, tipo `/be/gent/`.
- **Idiomas:**
  - neerlandés en el sitio (i18n);
  - palabras clave NL en la taxonomía;
  - hipótesis NLI en neerlandés, con prueba: el modelo de la Capa 2a no se entrenó con neerlandés (XNLI no lo incluye), así que hay que medir si transfiere bien.
- **Evaluar la UiTdatabank** (publiq, agenda cultural de Flandes con API): podría dar miles de eventos sin scraping, con Instagram como complemento para la escena independiente. Hay que verificar acceso, condiciones y cobertura. Lo mismo para París: OpenAgenda y el dataset "Que faire à Paris".

---

## Transversal (en cualquier momento)

- **Secretos:** sacar `.env` de la carpeta compartida (variables de entorno de Windows o `.env` externo) y agregar la regla de secretos al `CLAUDE.md`.
- **Decision log:** seguir en `docs/decisions_es.md` desde DD-076, y documentar los commits del 07-08/09 que no tienen DD.
- **Cloudflare:** revisar el Worker huérfano sin "-du".
- **RGPD / derechos:** se mantiene el gate de permiso de foto (fail-closed). Solo cuentas de organizaciones públicas, nunca personas individuales. Hay que revisar las condiciones de uso de HikerAPI y de Instagram antes de escalar.

## Orden y dependencias

```
Fase 0 Taxonomía ──┬──> Fase 2 Descubrimiento ──> Fase 3 Revisión ──> Fase 4 Pipeline ──> Fase 5 Sitio ──> Fase 6 Mantenimiento
Fase 1 HikerAPI ───┘                                                                                    └──> Fase 7 Gante
```

Las fases 0 y 1 pueden avanzar en paralelo. La 7 conviene prepararla desde la 2: la ciudad debe ser un parámetro en todo el código nuevo.
