"""M3b 新端点测试：源详情/新增/启停、事件、死信、健康、SSE。"""
from __future__ import annotations


def _sid(client, key="bbc-news"):
    resp = client.get("/api/v1/sources", params={})
    for s in resp.json()["items"]:
        if s["source_key"] == key:
            return s["id"]
    raise AssertionError("source not found")


def test_source_detail_and_patch(client):
    sid = _sid(client)
    d = client.get(f"/api/v1/sources/{sid}")
    assert d.status_code == 200
    assert "recent_fetch_log" in d.json() and "bound_proxy" in d.json()

    r = client.patch(f"/api/v1/sources/{sid}", json={"active": False,
                                                   "interval_minutes": 30})
    assert r.status_code == 200
    d2 = client.get(f"/api/v1/sources/{sid}").json()
    assert d2["active"] == 0 and d2["interval_minutes"] == 30
    assert client.get("/api/v1/sources/999999").status_code == 404


def test_add_source_validation(client):
    r = client.post("/api/v1/sources", json={
        "name": "T News", "base_url": "https://tnews.example",
        "country": "us", "language": "en",
        "feed_url": "https://tnews.example/rss"})
    assert r.status_code == 201
    assert r.json()["source_key"] == "tnews-example"
    # feed_url 重复 → 409
    r2 = client.post("/api/v1/sources", json={
        "name": "T2", "base_url": "https://t2.example", "country": "us",
        "language": "en", "feed_url": "https://tnews.example/rss"})
    assert r2.status_code == 409


def test_events_endpoints(client):
    app_db = client.app.state.settings.db_path
    import hashlib

    from app.db import WRITE_LOCK, connect
    from app.pipeline import utcnow
    with WRITE_LOCK, connect(app_db) as conn:
        cur = conn.execute(
            "INSERT INTO events (title, summary, article_count, first_seen,"
            " last_updated, organized) VALUES ('T 事件','综述',1,?,?,1)",
            (utcnow(), utcnow()))
        eid = cur.lastrowid
        src = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
        url = "https://example.com/ev-x"
        conn.execute(
            "INSERT INTO articles (source_id,url,url_hash,title,summary,"
            "language,fetched_at,event_id) VALUES (?,?,?,?,?,?,?,?)",
            (src["id"], url, hashlib.sha256(url.encode()).hexdigest(),
             "T 报道", "s", "en", utcnow(), eid))
    r = client.get("/api/v1/events")
    assert r.status_code == 200 and r.json()["total"] == 1
    d = client.get(f"/api/v1/events/{eid}")
    assert d.status_code == 200 and d.json()["members"][0]["title"] == "T 报道"
    assert client.get("/api/v1/events/999").status_code == 404
    r2 = client.get("/api/v1/events", params={"organized": 0})
    assert r2.json()["total"] == 0


def test_dead_letters_endpoint(client):
    app_db = client.app.state.settings.db_path
    from app.db import WRITE_LOCK, connect
    from app.pipeline import utcnow
    with WRITE_LOCK, connect(app_db) as conn:
        conn.execute(
            "INSERT INTO dead_letters (stage, article_id, payload, errors,"
            " status, created_at) VALUES ('llm_clean', NULL, '{}',"
            " '{\"e\":1}', 'open', ?)", (utcnow(),))
    r = client.get("/api/v1/dead-letters")
    assert r.status_code == 200 and r.json()["total"] == 1
    did = r.json()["items"][0]["id"]
    assert client.post(f"/api/v1/dead-letters/{did}/resolve").status_code == 200
    assert client.get("/api/v1/dead-letters",
                      params={"status": "open"}).json()["total"] == 0


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok"
    assert "llm_configured" in body and "sources_active" in body


def test_sse_stream_frames(client):
    """SSE 帧生成器：新 fetch_log 行产出事件帧，断连即停。"""
    import asyncio

    from app.api import sse_frames
    from app.db import WRITE_LOCK, connect
    from app.pipeline import utcnow

    app_db = client.app.state.settings.db_path
    with WRITE_LOCK, connect(app_db) as conn:
        src = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
        conn.execute(
            "INSERT INTO fetch_log (source_id, fetched_at, result,"
            " new_articles) VALUES (?,?,'ok',1)", (src["id"], utcnow()))

    frames = []
    calls = {"n": 0}

    def stop_after_two():
        calls["n"] += 1
        return calls["n"] >= 2

    async def collect():
        async for f in sse_frames(app_db, is_disconnected=stop_after_two,
                                  poll_seconds=0.01):
            frames.append(f)

    asyncio.run(collect())
    assert any("event: fetch" in f for f in frames)
    assert any(f == ": ping\n\n" for f in frames)
    assert '"ok"' in "".join(frames)
