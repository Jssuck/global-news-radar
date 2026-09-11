"""M4-F2 故障注入（单进程 MVP 等价语义）：

- LLM 不可用 → 清洗进入 DLQ，文章不丢（未整理/未清洗降级路径生效）；
- LLM 持续失败 → 纠错重试一次后仍进 DLQ（不重试风暴）；
- organize 环节异常 → organized=0 占位事件保留，前台新闻流仍可读；
- 轮询中一源抛异常 → 其余源继续抓取（worker 级故障隔离）。
"""
from __future__ import annotations

import asyncio
import hashlib

from app.db import WRITE_LOCK, connect
from app.pipeline import utcnow


def _mk_article(db_path, url="https://example.com/fault-1"):
    with WRITE_LOCK, connect(db_path) as conn:
        sid = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO articles (source_id,url,url_hash,title,summary,"
            "language,fetched_at,gate1_failed) VALUES (?,?,?,?,?,?,?,1)",
            (sid, url, hashlib.sha256(url.encode()).hexdigest(),
             "Fault Story", "raw body for cleaning", "en", utcnow()))
        return cur.lastrowid


class _DownProvider:
    """模拟 LLM 完全不可用（每次调用抛网络异常）。"""

    model = "down-mock"

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, *, max_tokens, response_format=None):
        self.calls += 1
        raise ConnectionError("llm endpoint unreachable")


def test_llm_outage_goes_to_dlq(db_path):
    """LLM 宕机：重试一次后写死信，文章记录不受影响。"""
    from app.llm import llm_clean_article
    aid = _mk_article(db_path)
    provider = _DownProvider()
    res = asyncio.run(llm_clean_article(
        db_path, provider, article_id=aid,
        raw_text="raw body for cleaning needs to be long enough",
        meta={"title": "Fault Story"}, reasons=["body_too_short"],
        fetched_at=utcnow()))
    assert res is None                       # 未产出清洗结果
    assert provider.calls == 2               # 恰好纠错重试一次（M2-F7）
    with connect(db_path) as conn:
        dlq = conn.execute(
            "SELECT * FROM dead_letters WHERE article_id=?", (aid,)).fetchall()
    assert len(dlq) == 1 and dlq[0]["status"] == "open"


def test_organize_failure_keeps_placeholder(db_path):
    """事件整理 LLM 失败 → organized=0 占位保留（降级路径，设计 M2c）。"""
    from app.events import organize_events
    now = utcnow()
    with WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO events (title, summary, article_count, first_seen,"
            " last_updated, organized) VALUES ('占位事件','',2,?,?,0)",
            (now, now))
        eid = cur.lastrowid
        sid = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()[0]
        for i in range(2):
            url = f"https://example.com/ev-{i}"
            conn.execute(
                "INSERT INTO articles (source_id,url,url_hash,title,summary,"
                "language,fetched_at,event_id) VALUES (?,?,?,?,?,?,?,?)",
                (sid, url, hashlib.sha256(url.encode()).hexdigest(),
                 f"M{i}", "s", "en", now, eid))
    asyncio.run(organize_events(db_path, _DownProvider(), now))
    with connect(db_path) as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    assert ev["organized"] == 0             # 占位保留，下轮重试（瞬态不入 DLQ）

    # 输出校验失败 → 死信
    class BadJson:
        model = "bad-json"
        async def chat(self, *a, **k):
            return "not json", {}

    asyncio.run(organize_events(db_path, BadJson(), now))
    with connect(db_path) as conn:
        dlq = conn.execute(
            "SELECT * FROM dead_letters WHERE stage='organize'").fetchall()
        ev2 = conn.execute("SELECT organized FROM events WHERE id=?",
                           (eid,)).fetchone()
    assert dlq and ev2["organized"] == 0


def test_fetch_one_isolates_source_failure(db_path, monkeypatch):
    """_fetch_one 内单源异常仅记日志，不拖垮并发批次（worker 级隔离）。"""
    import httpx

    from app.poller import _fetch_one
    from app.ratelimit import HostRateLimiter
    from app.robots import RobotsCache

    def boom_factory(timeout, user_agent, proxy=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.ConnectError("simulated crash",
                                           request=req)))
        client._mock_proxy = proxy
        return client

    monkeypatch.setattr("app.pipeline.default_client_factory", boom_factory)
    with connect(db_path) as conn:
        src = dict(conn.execute("SELECT * FROM sources LIMIT 1").fetchone())

    async def run():
        sem = asyncio.Semaphore(4)
        robots = RobotsCache(boom_factory(5, "ua"))
        limiter = HostRateLimiter(0)
        stop = asyncio.Event()
        # 全挂源：不应抛出
        await _fetch_one(db_path, src, _FakeSettings(), sem, robots,
                         limiter, stop)

    asyncio.run(run())  # 无异常即通过：故障被隔离在单源内


class _FakeSettings:
    """fetch_source 所需最小 settings 桩。"""
    request_timeout = 5
    user_agent = "gnr-test"
    max_articles_per_fetch = 5
    max_concurrency = 4
    per_host_min_interval = 0
    render_enabled = False
    render_daily_budget = 0
    gdelt_timespan = "24h"
    llm_base_url = None
    llm_model = ""
    llm_api_key_env = None
    llm_timeout = 5
    llm_max_tokens = 256
