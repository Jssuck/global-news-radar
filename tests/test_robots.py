"""robots.txt 合规测试：拦截、缓存 TTL、缺失放行（全离线 MockTransport）。"""
import asyncio

import httpx

from app.db import connect
from app.pipeline import fetch_source
from app.robots import RobotsCache
from tests.conftest import ARTICLE_HTML, FEED_XML

UA = "test-agent"


def run(coro):
    return asyncio.run(coro)


def _counting_factory(routes: dict[str, httpx.Response], counter: dict):
    """带请求计数的 mock 工厂，用于验证 robots 缓存命中。"""

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        counter[key] = counter.get(key, 0) + 1
        return routes.get(key) or httpx.Response(404, text="not found")

    def factory(timeout, user_agent, proxy=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_robots_disallow_blocks_fetch(db_path, source, settings_obj):
    """feed 所在域名 robots Disallow → 整源跳过并记 fetch_log。"""
    routes = {
        "https://feeds.bbci.co.uk/robots.txt": httpx.Response(
            200, text="User-agent: *\nDisallow: /\n"),
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(200, text=FEED_XML),
    }
    factory = _counting_factory(routes, {})
    result = run(fetch_source(db_path, source, settings_obj, factory,
                              robots=RobotsCache()))
    assert result["result"] == "robots_blocked"
    assert result["new_articles"] == 0
    with connect(db_path) as conn:
        logs = conn.execute(
            "SELECT * FROM fetch_log WHERE source_id = ?", (source["id"],)).fetchall()
        n_articles = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE source_id = ?",
            (source["id"],)).fetchone()[0]
    assert len(logs) == 1 and logs[0]["result"] == "robots_blocked"
    assert "robots" in logs[0]["detail"]
    assert n_articles == 0


def test_robots_disallow_blocks_article_pages(db_path, source, settings_obj):
    """feed 放行但文章页 host 禁止 → feed 解析成功、文章页跳过。"""
    routes = {
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(200, text=FEED_XML),
        "https://example.com/robots.txt": httpx.Response(
            200, text="User-agent: *\nDisallow: /news/\n"),
        "https://example.com/news/story-one": httpx.Response(200, text=ARTICLE_HTML),
        "https://example.com/news/story-two": httpx.Response(200, text=ARTICLE_HTML),
    }
    factory = _counting_factory(routes, {})
    result = run(fetch_source(db_path, source, settings_obj, factory,
                              robots=RobotsCache()))
    assert result["result"] == "ok"
    assert result["new_articles"] == 0
    assert result["robots_skipped"] == 2
    with connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert n == 0


def test_robots_result_cached_per_host(db_path, source, settings_obj):
    """同一 host 的 robots.txt 在 TTL 内只抓一次（跨源/跨文章共享缓存）。"""
    routes = {
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(200, text=FEED_XML),
        "https://example.com/robots.txt": httpx.Response(200, text="User-agent: *\nAllow: /\n"),
        "https://example.com/news/story-one": httpx.Response(200, text=ARTICLE_HTML),
        "https://example.com/news/story-two": httpx.Response(200, text=ARTICLE_HTML),
    }
    counter: dict = {}
    factory = _counting_factory(routes, counter)
    robots = RobotsCache(ttl_seconds=3600)
    run(fetch_source(db_path, source, settings_obj, factory, robots=robots))
    # 两篇文章 + 一次 fetch 判定，robots.txt 只抓一次
    assert counter.get("https://example.com/robots.txt") == 1
    # 缓存命中：直接判定不再发请求
    assert counter.get("https://example.com/news/story-one") == 1


def test_robots_cache_expiry_refetches():
    """TTL 过期后重新抓取 robots.txt。"""
    counter: dict = {}
    routes = {"https://example.com/robots.txt": httpx.Response(
        200, text="User-agent: *\nAllow: /\n")}
    factory = _counting_factory(routes, counter)
    robots = RobotsCache(ttl_seconds=-1)  # 立即过期

    async def _run():
        async with factory(10, UA) as client:
            await robots.allowed(client, "https://example.com/a", UA)
            await robots.allowed(client, "https://example.com/b", UA)

    run(_run())
    assert counter["https://example.com/robots.txt"] == 2


def test_missing_robots_allows_crawl():
    """robots.txt 404 → 放行。"""
    factory = _counting_factory({}, {})

    async def _run():
        async with factory(10, UA) as client:
            return await RobotsCache().allowed(
                client, "https://no-robots.example.com/x", UA)

    assert run(_run()) is True


def test_robots_403_disallows_all():
    """惯例：robots.txt 返回 401/403 视为全站禁止。"""
    routes = {"https://example.com/robots.txt": httpx.Response(403)}
    factory = _counting_factory(routes, {})

    async def _run():
        async with factory(10, UA) as client:
            return await RobotsCache().allowed(
                client, "https://example.com/x", UA)

    assert run(_run()) is False
