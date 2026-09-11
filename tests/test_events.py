"""M2c embedding 聚类 / 第三级 LLM 整理 / 渲染预算测试（离线）。"""
from __future__ import annotations

import asyncio
import json


def run(coro):
    return asyncio.run(coro)


def _insert_article(db_path, source_id, url, title, body=None):
    import hashlib

    from app.db import WRITE_LOCK, connect
    from app.pipeline import utcnow
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            "INSERT INTO articles (source_id, url, url_hash, title, summary,"
            " body, language, fetched_at) VALUES (?,?,?,?,?,?,?,?)",
            (source_id, url, hashlib.sha256(url.encode()).hexdigest(),
             title, title[:200], body or (title + " " * 250),
             "en", utcnow()))


def test_cosine_and_blob():
    from app.embed import blob_to_vec, cosine, vec_to_blob
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    assert cosine(a, b) == 1.0
    assert cosine(a, c) == 0.0
    blob = vec_to_blob([0.5, -1.25, 3.0])
    assert cosine(blob_to_vec(blob), [0.5, -1.25, 3.0]) > 0.999


def test_cluster_events_two_pass(db_path, source):
    """两组主题文章各自聚成事件；跨主题不合并；event_id 回写。"""
    from app.db import connect
    from app.embed import CallableEmbedder
    from app.events import cluster_events, embed_pending_articles
    from app.pipeline import utcnow

    topics = {
        "earthquake hits coastal city": "quake",
        "quake aftermath and rescue": "quake",
        "election results announced": "election",
        "opposition disputes election": "election",
    }

    def emb_fn(text):
        # 简易确定性 embedding：按关键词映射到正交向量
        t = text.lower()
        if "quake" in t or "earthquake" in t or "rescue" in t:
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedder = CallableEmbedder(emb_fn)
    now = utcnow()
    for title in topics:
        _insert_article(db_path, source["id"],
                        f"https://example.com/{title.replace(' ', '-')}", title)
    n = run(embed_pending_articles(db_path, embedder, now))
    assert n == 4
    events = cluster_events(db_path, now)
    assert events == 2
    with connect(db_path) as conn:
        arts = conn.execute(
            "SELECT a.title, a.event_id FROM articles a").fetchall()
    quake_events = {a["event_id"] for a in arts
                    if "quake" in a["title"] or "earthquake" in a["title"]}
    elect_events = {a["event_id"] for a in arts if "election" in a["title"]}
    assert len(quake_events) == 1 and len(elect_events) == 1
    assert quake_events != elect_events


def test_organize_events_llm(db_path, source):
    """≥2 成员事件送第三级 LLM → events 行更新 organized=1 + 标题/热度。"""
    import hashlib

    from app.db import WRITE_LOCK, connect
    from app.events import organize_events
    from app.llm import CallableProvider
    from app.pipeline import utcnow

    with WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO events (article_count, first_seen, last_updated,"
            " organized) VALUES (2, ?, ?, 0)", (utcnow(), utcnow()))
        eid = cur.lastrowid
        for i in (1, 2):
            url = f"https://example.com/ev-{i}"
            conn.execute(
                "INSERT INTO articles (source_id, url, url_hash, title,"
                " summary, body, language, fetched_at, event_id)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (source["id"], url, hashlib.sha256(url.encode()).hexdigest(),
                 f"Quake report {i}", "rescue ongoing", "x" * 300, "en",
                 utcnow(), eid))

    org_fn = lambda m: json.dumps({
        "title": "沿海城市地震", "summary": "多地报道救援进展",
        "iptc_tags": ["disaster"], "heat_score": 0.9})
    done = run(organize_events(db_path, CallableProvider(org_fn), utcnow()))
    assert done == 1
    with connect(db_path) as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    assert ev["organized"] == 1 and ev["title"] == "沿海城市地震"


def test_organize_events_llm_failure_keeps_placeholder(db_path, source):
    """LLM 输出非法 → organized=0 占位保留 + dead_letters 记录。"""
    import hashlib

    from app.db import WRITE_LOCK, connect
    from app.events import organize_events
    from app.llm import CallableProvider
    from app.pipeline import utcnow

    with WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO events (article_count, first_seen, last_updated,"
            " organized) VALUES (3, ?, ?, 0)", (utcnow(), utcnow()))
        eid = cur.lastrowid
        for i in range(3):
            url = f"https://example.com/f-{i}"
            conn.execute(
                "INSERT INTO articles (source_id, url, url_hash, title,"
                " summary, body, language, fetched_at, event_id)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (source["id"], url, hashlib.sha256(url.encode()).hexdigest(),
                 f"Evt {i}", "s", "x" * 300, "en", utcnow(), eid))

    done = run(organize_events(db_path,
                               CallableProvider(lambda m: "not json"),
                               utcnow()))
    assert done == 0
    with connect(db_path) as conn:
        ev = conn.execute("SELECT organized FROM events WHERE id=?",
                          (eid,)).fetchone()
        dlq = conn.execute(
            "SELECT stage FROM dead_letters WHERE stage='organize'").fetchone()
    assert ev["organized"] == 0 and dlq is not None


def test_render_budget_gate(db_path, settings_obj):
    """渲染预算：当日 rendered 计数达到上限后不再渲染。"""
    from app.db import WRITE_LOCK, connect
    from app.pipeline import utcnow
    from app.renderer import budget_ok, renders_today

    settings_obj.render_enabled = True
    settings_obj.render_daily_budget = 2
    assert budget_ok(db_path, settings_obj)
    with WRITE_LOCK, connect(db_path) as conn:
        src = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
        for _ in range(2):
            conn.execute(
                "INSERT INTO fetch_log (source_id, fetched_at, result)"
                " VALUES (?,?,'rendered')", (src["id"], utcnow()))
    assert renders_today(db_path) == 2
    assert not budget_ok(db_path, settings_obj)
