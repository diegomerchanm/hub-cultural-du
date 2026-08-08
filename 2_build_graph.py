import os
import json
from neo4j import GraphDatabase
from dotenv import load_dotenv

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
driver.verify_connectivity()
print("✅ Conexión exitosa a Neo4j Aura\n")


# ── 2. PERFIL ─────────────────────────────────────────────────────────────────
def load_profile(tx, p):
    is_private = p.get("private", False)

    # Propiedades base
    tx.run("""
        MERGE (a:Account {username: $username})
        ON CREATE SET a.firstSeenAt = datetime()
        SET a.lastUpdatedAt       = datetime(),
            a.id                  = $id,
            a.fullName            = $fullName,
            a.biography           = $biography,
            a.followersCount      = $followersCount,
            a.followsCount        = $followsCount,
            a.isBusinessAccount   = $isBusinessAccount,
            a.verified            = $verified,
            a.private             = $private,
            a.businessCategory    = $businessCategory,
            a.postsCount          = $postsCount,
            a.highlightReelCount  = $highlightReelCount,
            a.profilePicUrl       = $profilePicUrl
    """,
        username           = p.get("username"),
        id                 = p.get("id"),
        fullName           = p.get("fullName", ""),
        biography          = p.get("biography", ""),
        followersCount     = p.get("followersCount", 0),
        followsCount       = p.get("followsCount", 0),
        isBusinessAccount  = p.get("isBusinessAccount", False),
        verified           = p.get("verified", False),
        private            = is_private,
        businessCategory   = p.get("businessCategoryName", ""),
        postsCount         = p.get("postsCount", 0),
        highlightReelCount = p.get("highlightReelCount", 0),
        profilePicUrl      = p.get("profilePicUrl", ""),
    )

    # Label :Public o :Private — dos queries separadas, sin APOC
    if is_private:
        tx.run("MATCH (a:Account {username: $u}) SET a:Private", u=p.get("username"))
    else:
        tx.run("MATCH (a:Account {username: $u}) SET a:Public", u=p.get("username"))

    # Dirección como nodo Location vinculado al Account
    addr = p.get("businessAddress", {})
    if addr and addr.get("city_name"):
        tx.run("""
            MATCH (a:Account {username: $username})
            MERGE (l:Location {name: $city})
            ON CREATE SET l.firstSeenAt = datetime()
            SET l.lastUpdatedAt = datetime(),
                l.latitude       = $lat,
                l.longitude      = $lon,
                l.streetAddress  = $street,
                l.zipCode        = $zip
            MERGE (a)-[:LOCATED_AT]->(l)
        """,
            username = p.get("username"),
            city     = addr.get("city_name", ""),
            lat      = addr.get("latitude"),
            lon      = addr.get("longitude"),
            street   = addr.get("street_address", ""),
            zip      = addr.get("zip_code", ""),
        )

    # relatedProfiles → nodos Account + relación RELATED_TO
    for rp in p.get("relatedProfiles", []):
        if not rp.get("username"):
            continue
        tx.run("""
            MATCH (a:Account {username: $src})
            MERGE (b:Account {username: $dst})
            ON CREATE SET b.firstSeenAt = datetime()
            SET b.lastUpdatedAt = datetime(),
                b.id            = $id,
                b.fullName      = $fullName,
                b.verified      = $verified,
                b.private       = $private,
                b.profilePicUrl = $pic
            MERGE (a)-[:RELATED_TO]->(b)
        """,
            src      = p.get("username"),
            dst      = rp.get("username"),
            id       = rp.get("id", ""),
            fullName = rp.get("full_name", ""),
            verified = rp.get("is_verified", False),
            private  = rp.get("is_private", False),
            pic      = rp.get("profile_pic_url", ""),
        )


# ── 3. POSTS ──────────────────────────────────────────────────────────────────
def load_posts(tx, username, posts):
    for post in posts:
        pid = post.get("id")
        if not pid:
            continue

        # Nodo Post
        tx.run("""
            MATCH (a:Account {username: $username})
            MERGE (p:Post {id: $id})
            ON CREATE SET p.firstSeenAt = datetime()
            SET p.lastUpdatedAt      = datetime(),
                p.type               = $type,
                p.shortCode          = $shortCode,
                p.url                = $url,
                p.caption            = $caption,
                p.timestamp          = $timestamp,
                p.likesCount         = $likesCount,
                p.commentsCount      = $commentsCount,
                p.videoViewCount     = $videoViewCount,
                p.videoPlayCount     = $videoPlayCount,
                p.videoDuration      = $videoDuration,
                p.displayUrl         = $displayUrl,
                p.productType        = $productType,
                p.isCommentsDisabled = $isCommentsDisabled
            MERGE (a)-[:PUBLISHED]->(p)
        """,
            username           = username,
            id                 = pid,
            type               = post.get("type", ""),
            shortCode          = post.get("shortCode", ""),
            url                = post.get("url", ""),
            caption            = post.get("caption", ""),
            timestamp          = post.get("timestamp", ""),
            likesCount         = post.get("likesCount", 0),
            commentsCount      = post.get("commentsCount", 0),
            videoViewCount     = post.get("videoViewCount", 0),
            videoPlayCount     = post.get("videoPlayCount", 0),
            videoDuration      = post.get("videoDuration", 0.0) or 0.0,
            displayUrl         = post.get("displayUrl", ""),
            productType        = post.get("productType", ""),
            isCommentsDisabled = post.get("isCommentsDisabled", False),
        )

        # Hashtags
        for tag in post.get("hashtags", []):
            if not tag:
                continue
            tx.run("""
                MATCH (p:Post {id: $pid})
                MERGE (h:Hashtag {name: $tag})
                ON CREATE SET h.firstSeenAt = datetime()
                MERGE (p)-[:HAS_HASHTAG]->(h)
            """, pid=pid, tag=tag.lower())

        # Mentions → Account
        for mention in post.get("mentions", []):
            if not mention:
                continue
            tx.run("""
                MATCH (p:Post {id: $pid})
                MERGE (a:Account {username: $mention})
                ON CREATE SET a.firstSeenAt = datetime()
                MERGE (p)-[:MENTIONS]->(a)
            """, pid=pid, mention=mention)

        # TaggedUsers → Account
        for tu in post.get("taggedUsers", []):
            if not tu.get("username"):
                continue
            tx.run("""
                MATCH (p:Post {id: $pid})
                MERGE (a:Account {username: $username})
                ON CREATE SET a.firstSeenAt = datetime()
                SET a.lastUpdatedAt = datetime(),
                    a.id            = $id,
                    a.fullName      = $fullName,
                    a.verified      = $verified,
                    a.profilePicUrl = $pic
                MERGE (p)-[:TAGS_USER]->(a)
            """,
                pid      = pid,
                username = tu.get("username"),
                id       = tu.get("id", ""),
                fullName = tu.get("full_name", ""),
                verified = tu.get("is_verified", False),
                pic      = tu.get("profile_pic_url", ""),
            )

        # CoauthorProducers → Account
        for co in post.get("coauthorProducers", []):
            if not co.get("username"):
                continue
            tx.run("""
                MATCH (p:Post {id: $pid})
                MERGE (a:Account {username: $username})
                ON CREATE SET a.firstSeenAt = datetime()
                SET a.lastUpdatedAt = datetime(),
                    a.id            = $id,
                    a.verified      = $verified,
                    a.profilePicUrl = $pic
                MERGE (p)-[:COAUTHORED_BY]->(a)
            """,
                pid      = pid,
                username = co.get("username"),
                id       = co.get("id", ""),
                verified = co.get("is_verified", False),
                pic      = co.get("profile_pic_url", ""),
            )

        # Location
        loc_name = post.get("locationName")
        loc_id   = post.get("locationId")
        if loc_name:
            tx.run("""
                MATCH (p:Post {id: $pid})
                MERGE (l:Location {name: $name})
                ON CREATE SET l.firstSeenAt = datetime()
                SET l.locationId = $lid
                MERGE (p)-[:TAGGED_AT]->(l)
            """, pid=pid, name=loc_name, lid=loc_id or "")

        # Music
        music = post.get("musicInfo", {})
        if music and music.get("song_name"):
            tx.run("""
                MATCH (p:Post {id: $pid})
                MERGE (t:Track {audioId: $audioId})
                ON CREATE SET t.firstSeenAt = datetime()
                SET t.songName          = $song,
                    t.artistName        = $artist,
                    t.usesOriginalAudio = $original
                MERGE (p)-[:USES_MUSIC]->(t)
            """,
                pid      = pid,
                audioId  = music.get("audio_id", ""),
                song     = music.get("song_name", ""),
                artist   = music.get("artist_name", ""),
                original = music.get("uses_original_audio", False),
            )

        # Comments
        for c in post.get("latestComments", []):
            cid = c.get("id")
            if not cid:
                continue
            owner     = c.get("owner", {}) or {}
            commenter = c.get("ownerUsername") or owner.get("username")
            if not commenter:
                continue
            tx.run("""
                MATCH (p:Post {id: $pid})
                MERGE (a:Account {username: $username})
                ON CREATE SET a.firstSeenAt = datetime()
                MERGE (cm:Comment {id: $cid})
                ON CREATE SET cm.firstSeenAt = datetime()
                SET cm.text       = $text,
                    cm.timestamp  = $timestamp,
                    cm.likesCount = $likes
                MERGE (a)-[:WROTE]->(cm)
                MERGE (cm)-[:ON]->(p)
            """,
                pid       = pid,
                username  = commenter,
                cid       = cid,
                text      = c.get("text", ""),
                timestamp = c.get("timestamp", ""),
                likes     = c.get("likesCount", 0),
            )


# ── 4. IGTV VIDEOS ────────────────────────────────────────────────────────────
def load_igtv(tx, username, videos):
    for v in videos:
        vid = v.get("id")
        if not vid:
            continue

        tx.run("""
            MATCH (a:Account {username: $username})
            MERGE (iv:IgtvVideo {id: $id})
            ON CREATE SET iv.firstSeenAt = datetime()
            SET iv.lastUpdatedAt  = datetime(),
                iv.shortCode      = $shortCode,
                iv.title          = $title,
                iv.caption        = $caption,
                iv.url            = $url,
                iv.timestamp      = $timestamp,
                iv.likesCount     = $likesCount,
                iv.commentsCount  = $commentsCount,
                iv.videoViewCount = $videoViewCount,
                iv.videoDuration  = $videoDuration,
                iv.displayUrl     = $displayUrl
            MERGE (a)-[:PUBLISHED]->(iv)
        """,
            username       = username,
            id             = vid,
            shortCode      = v.get("shortCode", ""),
            title          = v.get("title", ""),
            caption        = v.get("caption", ""),
            url            = v.get("url", ""),
            timestamp      = v.get("timestamp", ""),
            likesCount     = v.get("likesCount", 0),
            commentsCount  = v.get("commentsCount", 0),
            videoViewCount = v.get("videoViewCount", 0),
            videoDuration  = v.get("videoDuration", 0.0) or 0.0,
            displayUrl     = v.get("displayUrl", ""),
        )

        for tag in v.get("hashtags", []):
            if not tag:
                continue
            tx.run("""
                MATCH (iv:IgtvVideo {id: $vid})
                MERGE (h:Hashtag {name: $tag})
                ON CREATE SET h.firstSeenAt = datetime()
                MERGE (iv)-[:HAS_HASHTAG]->(h)
            """, vid=vid, tag=tag.lower())

        for mention in v.get("mentions", []):
            if not mention:
                continue
            tx.run("""
                MATCH (iv:IgtvVideo {id: $vid})
                MERGE (a:Account {username: $mention})
                ON CREATE SET a.firstSeenAt = datetime()
                MERGE (iv)-[:MENTIONS]->(a)
            """, vid=vid, mention=mention)


# ── 5. PIPELINE GENÉRICO ──────────────────────────────────────────────────────
def process_account(username):
    data_dir     = "data_raw"
    profile_path = os.path.join(data_dir, f"profile_{username}.json")
    posts_path   = os.path.join(data_dir, f"posts_{username}.json")

    with driver.session() as session:

        # Perfil
        if os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            profile = data[0] if isinstance(data, list) else data

            session.execute_write(load_profile, profile)

            # latestPosts dentro del perfil
            latest = profile.get("latestPosts", [])
            if latest:
                session.execute_write(load_posts, username, latest)

            # IGTV
            igtv = profile.get("latestIgtvVideos", [])
            if igtv:
                session.execute_write(load_igtv, username, igtv)

            is_private = profile.get("private", False)
            label      = "🔒 Private" if is_private else "🌐 Public"
            print(f"  👤 {label} @{username} ({profile.get('followersCount', 0):,} followers)")
        else:
            print(f"  ⚠️  Sin perfil para @{username}")

        # Posts separados (más completos)
        if os.path.exists(posts_path):
            with open(posts_path, "r", encoding="utf-8") as f:
                posts = json.load(f)
            session.execute_write(load_posts, username, posts)
            print(f"  📸 {len(posts)} posts cargados")


# ── 6. MAIN ───────────────────────────────────────────────────────────────────
def main():
    data_dir = "data_raw"

    usernames = []
    for fname in os.listdir(data_dir):
        if fname.startswith("profile_") and fname.endswith(".json"):
            username = fname.replace("profile_", "").replace(".json", "")
            usernames.append(username)

    if not usernames:
        print("⚠️  No se encontraron archivos profile_*.json en data_raw/")
        return

    usernames.sort()
    print(f"🔍 {len(usernames)} perfiles detectados\n")

    public_count  = 0
    private_count = 0

    for username in usernames:
        process_account(username)
        # Conteo para resumen final
        profile_path = f"data_raw/profile_{username}.json"
        if os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            p = data[0] if isinstance(data, list) else data
            if p.get("private", False):
                private_count += 1
            else:
                public_count += 1

    driver.close()
    print(f"\n✅ Pipeline completo.")
    print(f"   🌐 Public : {public_count}")
    print(f"   🔒 Private: {private_count}")


if __name__ == "__main__":
    main()