"""
control_panel.py — Hub Cultural DU

Panel de control de la pipeline (v1, 2026-08-27, pedido de Diego): correr
cualquier script del proyecto desde el navegador, con sus propios argumentos
y su propio --dry-run, logs en vivo, e historial persistente de qué corrió,
cuándo, con qué argumentos y con qué resultado.

Decisión clave: los argumentos de cada script NO están hardcodeados acá. Se
leen por introspección del propio típer.Typer() de cada script (discover_
variants), así que un flag nuevo agregado a un script aparece solo en el
panel la próxima vez que se recarga la página — no hay una lista paralela
que mantener sincronizada a mano. Lo único manual es SCRIPT_REGISTRY: qué
scripts aparecen, en qué fase, y con qué descripción — eso sí es una
decisión editorial, no algo automatizable. Agregar un script nuevo al panel
es una línea en SCRIPT_REGISTRY, nada más.

v1 es deliberadamente chico (decisión explícita de Diego, "empezar chico"):
correr scripts + logs en vivo + historial. El dashboard de estado (conteos
en vivo desde Neo4j: cuántas cuentas scrapeadas, en qué fase está todo, qué
falta) y el botón de deploy (`wrangler deploy`) quedan para una entrega
futura.

Se importa desde review_events.py (pestaña "Panel de control") — no es una
app de Streamlit separada, para tener todo bajo un solo `streamlit run
review_events.py`. Ver render_control_panel(), llamada desde ahí.

Limitación conocida de v1: el seguimiento de un job "corriendo" en vivo
(log en tiempo real, estado) vive en st.session_state — solo funciona
mientras la pestaña del navegador que lo lanzó sigue abierta. Si cerrás esa
pestaña, el proceso en sí SIGUE corriendo en tu máquina hasta que termine
(no se mata), pero el panel deja de poder mostrar su progreso en vivo hasta
que termine — el log completo queda igual en .pipeline_runs/<run_id>.log,
revisable a mano. Esto es intencional para no meter una dependencia nueva
(tracking de PID entre sesiones es frágil multiplataforma) en la primera
entrega.
"""
import importlib
import json
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

try:
    import typer
    from typer.main import get_command
except ImportError:  # no debería pasar -- typer ya es dependencia del proyecto
    typer = None
    get_command = None

REPO_ROOT = Path(__file__).resolve().parent
RUNS_DIR = REPO_ROOT / ".pipeline_runs"
RUNS_DIR.mkdir(exist_ok=True)
HISTORY_FILE = RUNS_DIR / "history.jsonl"


# ── Registro editorial de scripts ────────────────────────────────────────
# "phase" agrupa visualmente en el orden real de la pipeline (ver CLAUDE.md
# "Commands"). "interactive_only" marca scripts que SIEMPRE piden
# confirmación por teclado (input()) sin ningún --dry-run que la evite --
# esos no se pueden correr sin colgarse desde un subprocess sin terminal
# real, así que el panel los muestra pero sin botón de correr, con una nota.
# "force_dry_run_only" marca scripts cuya corrida real pide escribir una
# palabra de confirmación por teclado (ej. "BORRAR") -- el panel solo
# habilita --dry-run para esos por ahora; la corrida real todavía se hace a
# mano en una terminal.
SCRIPT_REGISTRY = [
    {"phase": "1. Extracción", "path": "extract_profiles.py",
     "label": "Scrapear perfiles pendientes (Apify, cost-aware)",
     "interactive_only": True,
     "note": "Pide confirmar el costo estimado por teclado antes de gastar en Apify, siempre, sin --dry-run que lo evite. No se puede correr sin colgarse desde acá — corré `python extract_profiles.py` en tu propia terminal."},
    {"phase": "1. Extracción", "path": "1_harvest_ig_profiles.py",
     "label": "Scrapear perfiles (seeds curados)"},
    {"phase": "1. Extracción", "path": "1_harvest_ig_posts.py",
     "label": "Scrapear posts (seeds curados)"},
    {"phase": "1. Extracción", "path": "1_harvest_ig_posts_hikerapi.py",
     "label": "Posts vía HikerAPI (exploratorio, DD-059)"},
    {"phase": "2. Ingestión", "path": "2_build_graph.py",
     "label": "Cargar data_raw/*.json a Neo4j",
     "no_args": True,
     "note": "Sin argumentos ni --dry-run. Nota técnica: este script abre la conexión a Neo4j al nivel del módulo (no adentro de una función), así que el panel no lo importa para leer argumentos (rompería/tardaría de más) -- se corre directo."},
    {"phase": "3. Análisis", "path": "3_analyze_network.py",
     "label": "Análisis local de red (igraph/leidenalg)"},
    {"phase": "4. Categorización manual", "path": "load_manual_account_categorization.py",
     "label": "Subir planilla curada (incluye photoPermission, DD-060)"},
    {"phase": "4. Categorización manual", "path": "seal_legacy_batch.py",
     "label": "Sellar batch legacy (histórico, dormant en la práctica)"},
    {"phase": "5. Eventos", "path": "4_enrich_events_extract.py",
     "label": "Detectar eventos nuevos (Capa 1/2/3 + gating)"},
    {"phase": "5. Eventos", "path": "4_enrich_events_resolve.py",
     "label": "Dedup de eventos existentes"},
    {"phase": "5. Eventos", "path": "backfill_art_tags_fr.py",
     "label": "Backfill: traducir vocabulario de eventArtTags"},
    {"phase": "5. Eventos", "path": "backfill_geo_zone.py",
     "label": "Backfill: heredar geoZone de cuentas ya curadas (Pieza A, DD-070)"},
    {"phase": "5. Eventos", "path": "backfill_event_images.py",
     "label": "Backfill: imageUrl en eventos existentes (DD-057)"},
    {"phase": "6. Geo", "path": "4_enrich_locations.py",
     "label": "Geocodificar ubicaciones (Nominatim)"},
    {"phase": "7. Limpieza", "path": "cleanup_legacy_accounts.py",
     "label": "Borrar cuentas sin categorización manual",
     "force_dry_run_only": True,
     "note": "La corrida real pide escribir \"BORRAR\" por teclado — desde acá solo se habilita --dry-run (conteos exactos, con ROLLBACK, no borra nada). Para la corrida real, todavía hay que hacerla vos desde una terminal."},
    {"phase": "8. Export", "path": "export_events_excel.py",
     "label": "Exportar eventos a Excel"},
    {"phase": "9. Publicación", "path": "5_export_dashboard_data.py",
     "label": "Regenerar site/data.json (correr después de aprobar en Streamlit)"},
]


# ── Introspección de argumentos vía Typer/Click ──────────────────────────
# Típer agrega estos dos automáticamente a cualquier app de un solo comando
# que no pase add_completion=False -- son helpers de autocompletado de shell,
# no argumentos reales del script. Si no se filtran, aparecen como checkboxes
# falsos en el formulario auto-generado (confirmado corriendo esto contra
# los scripts reales del repo: 5_export_dashboard_data.py,
# load_manual_account_categorization.py, seal_legacy_batch.py y
# cleanup_legacy_accounts.py los traen; los que ya pasan add_completion=False
# no).
_SKIP_PARAM_NAMES = {"install_completion", "show_completion"}


def _extract_params(click_cmd):
    out = []
    for p in click_cmd.params:
        if not getattr(p, "opts", None) or p.name in _SKIP_PARAM_NAMES:
            continue
        flag = max(p.opts, key=len)  # el nombre largo, ej. --threshold
        out.append({
            "flag": flag,
            "name": p.name,
            "help": p.help or "",
            "default": p.default,
            "is_flag": bool(getattr(p, "is_flag", False)),
            "required": bool(getattr(p, "required", False)),
            "type": p.type.name if hasattr(p.type, "name") else str(p.type),
        })
    return out


@st.cache_resource(show_spinner=False)
def discover_variants(rel_path: str):
    """
    Devuelve (variants, error). variants es una lista de dicts
    {"subcommand": str|None, "params": [...]}: una entrada por subcomando si
    el script tiene varios (ej. 1_harvest_ig_posts_hikerapi.py ->
    calibrate/compare/harvest), o una sola con subcommand=None si es un
    único comando o si el script no usa Typer (en ese caso, params=[]).

    Cacheado con st.cache_resource -- importar cada script una sola vez por
    sesión del panel, no en cada rerun (varios scripts importan spaCy/torch/
    sentence-transformers a nivel de módulo indirectamente y tardan según
    corresponda).
    """
    module_name = rel_path[:-3]  # sin ".py"
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:
        return None, f"No se pudo importar {rel_path} para leer sus argumentos: {type(e).__name__}: {e}"

    app = getattr(mod, "app", None)
    if app is None or typer is None or not isinstance(app, typer.Typer):
        return [{"subcommand": None, "params": []}], None

    cmd = get_command(app)
    if hasattr(cmd, "commands") and cmd.commands:
        variants = [{"subcommand": name, "params": _extract_params(sub)} for name, sub in cmd.commands.items()]
        return variants, None
    return [{"subcommand": None, "params": _extract_params(cmd)}], None


# ── Historial (JSONL append-only, .pipeline_runs/history.jsonl) ─────────
def _append_history(record: dict):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_history(limit: int = 200):
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    records.reverse()  # más reciente primero
    return records


def _update_history_status(run_id: str, status: str, returncode):
    """Reescribe el archivo entero -- el historial es chico (un run por
    click), no hace falta nada más sofisticado."""
    if not HISTORY_FILE.exists():
        return
    lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if rec.get("run_id") == run_id:
            rec["status"] = status
            rec["returncode"] = returncode
            rec["finished_at"] = datetime.now().isoformat(timespec="seconds")
        out.append(json.dumps(rec, ensure_ascii=False))
    HISTORY_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


# ── Ejecución ─────────────────────────────────────────────────────────────
def _launch(entry, subcommand, args, dry_run_flag_present):
    run_id = uuid.uuid4().hex[:12]
    log_path = RUNS_DIR / f"{run_id}.log"
    cmd = [sys.executable, entry["path"]]
    if subcommand:
        cmd.append(subcommand)
    cmd.extend(args)

    log_fh = open(log_path, "w", encoding="utf-8")
    log_fh.write(f"$ {' '.join(cmd)}\n\n")
    log_fh.flush()
    proc = subprocess.Popen(
        cmd, cwd=REPO_ROOT, stdout=log_fh, stderr=subprocess.STDOUT, text=True,
    )
    record = {
        "run_id": run_id,
        "script": entry["path"],
        "subcommand": subcommand,
        "args": args,
        "dry_run": dry_run_flag_present,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running",
        "log_path": str(log_path),
    }
    _append_history(record)
    st.session_state["running_job"] = {"proc": proc, "log_fh": log_fh, "record": record}


def _render_running_job():
    job = st.session_state.get("running_job")
    if not job:
        return False
    proc, log_fh, record = job["proc"], job["log_fh"], job["record"]
    ret = proc.poll()
    st.subheader("⏳ Corriendo…" if ret is None else ("✅ Terminó" if ret == 0 else "❌ Terminó con error"))
    cmd_str = " ".join([sys.executable, record["script"]] + ([record["subcommand"]] if record["subcommand"] else []) + record["args"])
    st.caption(cmd_str)
    log_text = Path(record["log_path"]).read_text(encoding="utf-8", errors="replace")
    st.code(log_text[-6000:] or "(sin salida todavía)", language="text")
    if ret is None:
        st.button("🔄 Actualizar log", key="refresh_log")
        st.caption("Sigue corriendo en tu máquina aunque cierres esta pestaña -- el log completo queda en .pipeline_runs/.")
    else:
        log_fh.close()
        _update_history_status(record["run_id"], "success" if ret == 0 else "failed", ret)
        if st.button("Cerrar", key="close_job"):
            del st.session_state["running_job"]
            st.rerun()
    return True


def _build_args(params, values):
    """Traduce {nombre_param: valor del formulario} a una lista de args de
    línea de comandos (['--threshold', '0.6', '--dry-run', ...]). Separado de
    render_control_panel() para poder testearlo sin Streamlit."""
    args = []
    dry_run_present = False
    for p in params:
        v = values.get(p["name"])
        if p["is_flag"]:
            if v:
                args.append(p["flag"])
                if p["name"] == "dry_run":
                    dry_run_present = True
        elif v not in (None, ""):
            args.extend([p["flag"], str(v)])
    return args, dry_run_present


# ── Formulario auto-generado por script ──────────────────────────────────
def _render_param_input(entry, variant_key, p):
    key = f"{variant_key}__{p['name']}"
    label = f"{p['flag']}" + (" (requerido)" if p["required"] else "")
    help_text = p["help"] or None
    if p["is_flag"]:
        return st.checkbox(label, value=bool(p["default"]), help=help_text, key=key)
    if p["type"] == "int":
        return st.number_input(label, value=int(p["default"]) if p["default"] is not None else 0, step=1, help=help_text, key=key)
    if p["type"] == "float":
        return st.number_input(label, value=float(p["default"]) if p["default"] is not None else 0.0, help=help_text, key=key)
    return st.text_input(label, value="" if p["default"] is None else str(p["default"]), help=help_text, key=key)


def render_control_panel():
    st.title("🎛️ Panel de control de la pipeline")
    st.caption(
        "Corré cualquier fase de la pipeline desde acá, con dry-run cuando el script lo soporta. "
        "Los argumentos de cada script se leen automáticamente de su propio código -- no son una lista fija."
    )

    if _render_running_job():
        return  # mientras hay un job activo, no mostrar el formulario de al lado

    options = [f"{e['phase']} · {e['label']}" for e in SCRIPT_REGISTRY]
    choice = st.selectbox("Elegí un script", options, key="cp_script_choice")
    entry = SCRIPT_REGISTRY[options.index(choice)]

    st.markdown(f"**`{entry['path']}`**")
    if entry.get("note"):
        st.info(entry["note"])

    if entry.get("interactive_only"):
        return  # no hay nada más para mostrar -- ver la nota de arriba

    if entry.get("no_args"):
        variants, error = [{"subcommand": None, "params": []}], None
    else:
        variants, error = discover_variants(entry["path"])
    if error:
        st.error(error)
        return

    subcommand = None
    if len(variants) > 1:
        sub_names = [v["subcommand"] for v in variants]
        subcommand = st.radio("Subcomando", sub_names, horizontal=True, key=f"sub_{entry['path']}")
        variant = next(v for v in variants if v["subcommand"] == subcommand)
    else:
        variant = variants[0]

    variant_key = f"{entry['path']}::{subcommand or ''}"
    with st.form(key=f"form_{variant_key}"):
        values = {}
        missing_required = []
        force_dry_run = entry.get("force_dry_run_only")
        for p in variant["params"]:
            if force_dry_run and p["name"] == "dry_run":
                st.checkbox(f"{p['flag']} (forzado en este panel, ver nota arriba)", value=True, disabled=True, key=f"{variant_key}__dry_run_locked")
                values[p["name"]] = True
                continue
            values[p["name"]] = _render_param_input(entry, variant_key, p)
            if p["required"] and not values[p["name"]]:
                missing_required.append(p["flag"])
        if not variant["params"]:
            st.caption("Este script no tiene argumentos.")
        submitted = st.form_submit_button("▶️ Correr")

    if submitted:
        if missing_required:
            st.error("Falta completar: " + ", ".join(missing_required))
            return
        args, dry_run_present = _build_args(variant["params"], values)
        _launch(entry, subcommand, args, dry_run_present)
        st.rerun()

    st.divider()
    st.subheader("Historial reciente de este script")
    hist = [r for r in _read_history(200) if r["script"] == entry["path"]]
    if not hist:
        st.caption("Todavía no se corrió desde el panel.")
    else:
        st.dataframe(
            [{
                "cuándo": r["started_at"], "subcomando": r.get("subcommand") or "",
                "args": " ".join(r.get("args", [])), "estado": r.get("status"),
                "código": r.get("returncode"),
            } for r in hist[:15]],
            use_container_width=True, hide_index=True,
        )

    with st.expander("Ver historial completo de todos los scripts"):
        all_hist = _read_history(200)
        if not all_hist:
            st.caption("Vacío todavía.")
        else:
            st.dataframe(
                [{
                    "cuándo": r["started_at"], "script": r["script"], "subcomando": r.get("subcommand") or "",
                    "args": " ".join(r.get("args", [])), "estado": r.get("status"), "código": r.get("returncode"),
                } for r in all_hist],
                use_container_width=True, hide_index=True,
            )
