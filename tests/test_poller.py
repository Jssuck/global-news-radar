"""自适应轮询间隔与 per-host 限速测试（全离线）。"""
import asyncio
import time

import httpx

from app.db import connect
from app.pipeline import (
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    adjust_interval,
    fetch_source,
)
from app.ratelimit import HostRateLimiter
from app.robots import RobotsCache
from tests.conftest import FEED_XML, make_mock_client_factory


def run(coro):
    return asyncio.run(coro)


# ---- 自适应间隔纯函数 ----

def test_new_articles_shrink_interval_and_reset_streak():
    interval, streak = adjust_interval(10, new_articles=3, empty_streak=5)
    assert interval == 7  # 10 / 1.5 ≈ 6.67 → 7
    assert streak == 0


def test_interval_floor_at_5_minutes():
    assert adjust_interval(5, new_articles=1, empty_streak=0)[0] == MIN_INTERVAL_MINUTES
    assert adjust_interval(6, new_articles=1, empty_streak=0)[0] == MIN_INTERVAL_MINUTES


def test_first_empty_round_keeps_interval():
    interval, streak = adjust_interval(10, new_articles=0, empty_streak=0)
    assert interval == 10 and streak == 1  # 首轮无新文章只累计轮数


def test_second_empty_round_grows_interval():
    interval, streak = adjust_interval(10, new_articles=0, empty_streak=1)
    assert interval == 15 and streak == 2  # 连续 2 轮无新文章 ×1.5


def test_interval_ceiling_at_60_minutes():
    assert adjust_interval(60, new_articles=0, empty_streak=2)[0] == MAX_INTERVAL_MINUTES
    assert adjust_interval(50, new_articles=0, empty_streak=1)[0] == MAX_INTERVAL_MINUTES


# ---- 自适应间隔落库（端到端） ----

def test_adaptive_interval_persisted(db_path, source, settings_obj):
    """bbc 初始 5 分钟；连续两轮无新文章后 ×1.5 → 8，streak=2。"""
    empty_feed = FEED_XML.replace("<item>", "<!--").replace("</item>", "-->")
    routes = {"https://feeds.bbci.co.uk/news/rss.xml":
              httpx.Response(200, text=empty_feed)}  # 无 ETag，不走 304
    factory = make_mock_client_factory(routes)
    assert source["interval_minutes"] == 5

    r1 = run(fetch_source(db_path, source, settings_obj, factory,
                          robots=RobotsCache()))
    assert r1["result"] == "ok" and r1["new_articles"] == 0
    with connect(db_path) as conn:
        row = conn.execute("SELECT interval_minutes, empty_streak FROM sources"
                           " WHERE id = ?", (source["id"],)).fetchone()
    assert row["interval_minutes"] == 5 and row["empty_streak"] == 1

    src2 = dict(source) | dict(row)
    run(fetch_source(db_path, src2, settings_obj, factory, robots=RobotsCache()))
    with connect(db_path) as conn:
        row = conn.execute("SELECT interval_minutes, empty_streak FROM sources"
                           " WHERE id = ?", (source["id"],)).fetchone()
    assert row["interval_minutes"] == 8 and row["empty_streak"] == 2  # 5×1.5=7.5→8


# ---- per-host 限速 ----

def test_rate_limiter_enforces_min_interval():
    limiter = HostRateLimiter(min_interval=0.2)

    async def _run():
        await limiter.acquire("https://example.com/a")
        t0 = time.monotonic()
        await limiter.acquire("https://example.com/b")
        return time.monotonic() - t0

    assert run(_run()) >= 0.19  # 同 host 第二次请求被推迟到最小间隔后


def test_rate_limiter_different_hosts_not_blocked():
    limiter = HostRateLimiter(min_interval=0.5)

    async def _run():
        await limiter.acquire("https://a.example.com/x")
        t0 = time.monotonic()
        await limiter.acquire("https://b.example.com/y")
        return time.monotonic() - t0

    assert run(_run()) < 0.2  # 不同 host 互不阻塞


def test_rate_limiter_disabled_with_zero_interval():
    limiter = HostRateLimiter(min_interval=0)

    async def _run():
        t0 = time.monotonic()
        for i in range(5):
            await limiter.acquire(f"https://example.com/{i}")
        return time.monotonic() - t0

    assert run(_run()) < 0.1
