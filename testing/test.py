import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
d = GraphDatabase.driver(os.getenv("NEO4J_URI"), auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")))

pairs = [
    ("evt_f1a7224ba3d3", "evt_9146739c1646"),
    ("evt_dc91acf25685", "evt_9c98a67f7c8e"),
]

with d.session() as s:
    for a, b in pairs:
        print("=" * 70)
        print(f"PAR: {a}  <->  {b}")
        print("=" * 70)
        for eid in (a, b):
            rows = s.run("""
                MATCH (p:Post)-[:MENTIONS_EVENT]->(e:Event {id: $eid})
                RETURN e.title AS title, e.category AS category, e.eventDate AS eventDate,
                       e.locationName AS loc, e.description AS description,
                       collect({author: p.author, caption: p.caption, timestamp: p.timestamp}) AS posts
            """, eid=eid).data()
            if not rows:
                print(f"  [{eid}] sin posts conectados")
                continue
            r = rows[0]
            print(f"\n  [{eid}] {r['title']}  (cat={r['category']})")
            print(f"    fecha={r['eventDate'] or '-'}  loc={r['loc'] or '-'}")
            print(f"    descripcion: {r['description'] or '-'}")
            for p in r["posts"][:3]:
                print(f"    -- @{p['author']} ({p['timestamp']}): {(p['caption'] or '')[:200].strip()}")
        print()
d.close()