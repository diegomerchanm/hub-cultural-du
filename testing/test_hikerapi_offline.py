"""
testing/test_hikerapi_offline.py — Prueba offline (sin red, sin gasto) de la
migración a HikerAPI (DD-076). Simula las respuestas de HikerAPI según su doc
y verifica: normalización de perfiles y posts al shape de Apify, exclusiones,
caché de user_id, ventana dinámica, fusión y tope de 50, tope de gasto, y que
load_profile/load_posts de 2_build_graph.py consumen los archivos sin error.

    python testing/test_hikerapi_offline.py
"""
import sys, json, ast, importlib.util, os, tempfile, shutil
from datetime import datetime, timedelta, timezone
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, REPO)
os.environ["HIKERAPI_ACCESS_KEY"] = "test-dummy"   # clave falsa: nunca se llama a la red
_tmp = tempfile.mkdtemp(prefix="hiker_test_"); os.chdir(_tmp)
os.makedirs("data_raw"); os.makedirs("config")
shutil.copy(os.path.join(REPO, "config", "excluded_accounts.json"), "config/")
json.dump({"seeds": [{"handle": "cuenta_a"}, {"handle": "@cuenta_b"}, {"handle": "cuenta_a"},
                     {"handle": "sortiraparis.officiel"}]}, open("config/seeds.json", "w"))
import hikerapi_client as hc

now = datetime.now(timezone.utc)
def media(pk, days_ago, cap="Concierto #jazz con @amigo.x el 12/10"):
    return {"pk": pk, "code": f"C{pk}", "media_type": 8, "taken_at": (now - timedelta(days=days_ago)).isoformat(),
            "caption_text": cap, "like_count": 5, "image_versions": [{"url": f"https://img/{pk}"}],
            "usertags": {"in": [{"user": {"username": "tagged1", "pk": 9}}]}, "location": {"pk": 77, "name": "Le Lieu"},
            "user": {"username": "cuenta_a", "full_name": "A"}}
USERS = {"cuenta_a": {"pk": 111, "username": "cuenta_a", "full_name": "Cuenta A", "follower_count": 1234,
                      "following_count": 10, "media_count": 99, "is_private": False, "is_business": True,
                      "category_name": "Theater", "city_name": "Paris", "latitude": 48.8, "longitude": 2.3,
                      "address_street": "1 rue X", "zip": "75011", "profile_pic_url_hd": "https://pic"},
         "cuenta_b": {"pk": 222, "username": "cuenta_b", "is_private": True, "follower_count": 5}}
calls = []
def fake_get(self, path, params):
    self._check_budget(); self.requests_used += 1; calls.append((path, dict(params)))
    if path == "/v1/user/by/username": return USERS[params["username"]]
    if path == "/v1/user/medias/chunk":
        if params["user_id"] == "111":
            if "end_cursor" not in params: return [[media(i, i) for i in range(1, 13)], "cur1"]
            return [[media(i, i) for i in range(13, 25)], None]
        return [[], None]
hc.HikerClient.get = fake_get

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
prof = load("prof", f"{REPO}/1_harvest_ig_profiles_hikerapi.py")
posts = load("posts", f"{REPO}/1_harvest_ig_posts_hikerapi.py")

# 1) perfiles
prof.harvest(seeds="config/seeds.json", pending_from_neo4j=False, force=False, max_usd=1.0, dry_run=False, yes=True)
p = json.load(open("data_raw/profile_cuenta_a.json"))[0]
assert p["followersCount"] == 1234 and p["id"] == "111" and p["businessCategoryName"] == "Theater"
assert p["businessAddress"]["city_name"] == "Paris" and p["businessAddress"]["zip_code"] == "75011"
assert not os.path.exists("data_raw/profile_sortiraparis.officiel.json"), "excluida no debe scrapearse"
assert json.load(open("data_raw/hikerapi_user_ids.json"))["cuenta_a"] == "111"
n_prof_calls = len(calls)

# 2) posts: ventana 10 días, pk en caché => 0 llamadas a by/username
posts.harvest(seeds="config/seeds.json", max_days=10, force=False, max_usd=1.0, dry_run=False, yes=True)
assert not any(c[0] == "/v1/user/by/username" for c in calls[n_prof_calls:]), "debería usar la caché de pk"
ps = json.load(open("data_raw/posts_cuenta_a.json"))
assert all((now - datetime.fromisoformat(x["timestamp"].replace("Z","+00:00"))).days <= 10 for x in ps), "ventana"
assert len(ps) in (9, 10), len(ps)
x = ps[0]
assert x["timestamp"].endswith(".000Z") and x["hashtags"] == ["jazz"] and x["mentions"] == ["amigo.x"]
assert x["taggedUsers"][0]["username"] == "tagged1" and x["locationName"] == "Le Lieu" and x["displayUrl"]
assert x["type"] == "Sidecar"
# 3) re-corrida inmediata: brecha < 1 día => se salta, 0 requests
before = len(calls)
posts.harvest(seeds="config/seeds.json", max_days=10, force=False, max_usd=1.0, dry_run=False, yes=True)
assert sum(c[1].get("user_id") == "111" for c in calls[before:]) == 1, "re-corrida: ventana corta, 1 sola página"
# 4) fusión: archivo viejo con posts de Apify se conserva y se deduplica
old = [{"id": "5", "timestamp": "2020-01-01T00:00:00.000Z", "caption": "viejo apify"}, {"id": "999", "timestamp": "2020-01-02T00:00:00.000Z"}]
merged = posts.merge_and_cap(old, ps)
assert len(merged) == len(ps) + 1 and merged[-1]["id"] == "999"
assert next(m for m in merged if m["id"] == "5")["caption"] != "viejo apify", "el nuevo debe ganar"
assert {m["id"] for m in merged} >= {"999"}
# 5) tope de gasto
c = hc.HikerClient(max_requests=2)
c.get("/v1/user/by/username", {"username": "cuenta_a"}); c.get("/v1/user/by/username", {"username": "cuenta_a"})
try:
    c.get("/v1/user/by/username", {"username": "cuenta_a"}); raise SystemExit("FALLO: no frenó el tope")
except hc.BudgetExceeded: pass
# 6) days_to_fetch
assert posts.days_to_fetch([], 10) == 10
assert posts.days_to_fetch([{"timestamp": (now - timedelta(hours=5)).isoformat()}], 10) is None
assert posts.days_to_fetch([{"timestamp": (now - timedelta(days=3, hours=1)).isoformat()}], 10) == 4
assert posts.days_to_fetch([{"timestamp": "2020-01-01T00:00:00Z"}], 10) == 10

# 7) ingestión: ejecutar load_profile/load_posts reales de 2_build_graph con un tx falso
src = open(f"{REPO}/2_build_graph.py", encoding="utf-8").read()
tree = ast.parse(src)
ns = {}
for node in tree.body:
    if isinstance(node, (ast.FunctionDef,)) and node.name in ("load_profile", "load_posts"):
        exec(compile(ast.Module([node], []), "2_build_graph.py", "exec"), ns)
class Tx:
    def __init__(s): s.n = 0; s.params = []
    def run(s, q, **kw): s.n += 1; s.params.append(kw)
tx = Tx(); ns["load_profile"](tx, p); assert tx.n >= 2
tx2 = Tx(); ns["load_posts"](tx2, "cuenta_a", ps, "dedicated_scraper"); assert tx2.n > len(ps)
print(f"OK · {len(calls)} llamadas simuladas · load_profile {tx.n} queries · load_posts {tx2.n} queries")
