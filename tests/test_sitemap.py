"""sitemap 二级发现测试：解析、index 嵌套、lastmod 过滤、管线集成（全离线）。"""
import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.db import connect
from app.pipeline import fetch_source
from app.robots import RobotsCache
from app.sitemap import discover_urls, filter_recent, parse_lastmod, parse_sitemap
from tests.conftest import ARTICLE_HTML, make_mock_client_factory

# 用真实当前时间生成 lastmod（管线内部按真实时间过滤 48h 窗口）
NOW = datetime.now(UTC)
RECENT = (NOW - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
OLD = (NOW - timedelta(hours=72)).isoformat().replace("+00:00", "Z")

SITEMAP_INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-news.xml</loc><lastmod>{RECENT}</lastmod></sitemap>
  <sitemap><loc>https://example.com/sitemap-archive.xml</loc></sitemap>
</sitemapindex>
"""

URLSET_NEWS = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/news/story-one</loc><lastmod>{RECENT}</lastmod></url>
  <url><loc>https://example.com/news/story-old</loc><lastmod>{OLD}</lastmod></url>
  <url><loc>https://example.com/news/story-two</loc></url>
</urlset>
"""

URLSET_ARCHIVE = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/archive/item</loc><lastmod>2020-01-01</lastmod></url>
</urlset>
"""

ROBOTS_TXT = "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml\n"


def run(coro):
    return asyncio.run(coro)


# ---- 解析与过滤（纯函数） ----

def test_parse_urlset_with_lastmod():
    parsed = parse_sitemap(URLSET_NEWS)
    assert parsed["type"] == "urlset"
    assert len(parsed["urls"]) == 3
    by_loc = dict(parsed["urls"])
    assert by_loc["https://example.com/news/story-one"] is not None
    assert by_loc["https://example.com/news/story-two"] is None  # 无 lastmod


def test_parse_sitemap_index():
    parsed = parse_sitemap(SITEMAP_INDEX)
    assert parsed["type"] == "sitemapindex"
    locs = [loc for loc, _ in parsed["sitemaps"]]
    assert "https://example.com/sitemap-news.xml" in locs
    assert "https://example.com/sitemap-archive.xml" in locs


def test_parse_lastmod_variants():
    assert parse_lastmod("2026-06-02T07:00:00Z").tzinfo is not None
    assert parse_lastmod("2026-06-02T07:00:00+00:00") is not None
    assert parse_lastmod("2026-06-02") is not None
    assert parse_lastmod("not-a-date") is None
    assert parse_lastmod(None) is None


def test_filter_recent_window_and_missing_lastmod():
    urls = parse_sitemap(URLSET_NEWS)["urls"]
    recent = filter_recent(urls, now=NOW)
    locs = [loc for loc, _ in recent]
    # 近 48h 保留；72h 前的剔除；无 lastmod 的放行（哈希去重兜底）
    assert "https://example.com/news/story-one" in locs
    assert "https://example.com/news/story-two" in locs
    assert "https://example.com/news/story-old" not in locs


# ---- 在线发现（MockTransport 离线） ----

def _routes_with_index():
    return {
        "https://example.com/robots.txt": httpx.Response(200, text=ROBOTS_TXT),
        "https://example.com/sitemap.xml": httpx.Response(200, text=SITEMAP_INDEX),
        "https://example.com/sitemap-news.xml": httpx.Response(200, text=URLSET_NEWS),
        "https://example.com/sitemap-archive.xml": httpx.Response(200, text=URLSET_ARCHIVE),
    }


def test_discover_urls_via_robots_sitemap_directive():
    """无显式 sitemap_url 时走 robots.txt 的 Sitemap 指令，且展开 index 嵌套。"""
    source = {"source_key": "demo", "base_url": "https://example.com",
              "sitemap_url": None}
    factory = make_mock_client_factory(_routes_with_index())
    robots = RobotsCache()

    async def _run():
        async with factory(10, "test-agent") as client:
            return await discover_urls(client, source, robots=robots, now=NOW)

    found = run(_run())
    locs = [loc for loc, _ in found]
    # index 嵌套展开 + lastmod 过滤 + 无 lastmod 放行
    assert "https://example.com/news/story-one" in locs
    assert "https://example.com/news/story-two" in locs
    assert "https://example.com/news/story-old" not in locs
    # archive 子 sitemap 中 2020 年的 URL 被 lastmod 提示过滤
    assert "https://example.com/archive/item" not in locs


def test_discover_urls_explicit_sitemap_url():
    """显式 sitemap_url 优先，不查 robots。"""
    source = {"source_key": "demo", "base_url": "https://example.com",
              "sitemap_url": "https://example.com/sitemap-news.xml"}
    factory = make_mock_client_factory(_routes_with_index())

    async def _run():
        async with factory(10, "test-agent") as client:
            return await discover_urls(client, source, now=NOW)

    locs = [loc for loc, _ in run(_run())]
    assert locs == ["https://example.com/news/story-one",
                    "https://example.com/news/story-two"]


# ---- 管线集成：sitemap 策略源端到端 ----

def _insert_sitemap_source(db_path) -> dict:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO sources (source_key, name, base_url, country, language,
                                 feed_url, interval_minutes, discovery_strategy,
                                 sitemap_url)
            VALUES ('demo-sitemap', 'Demo Sitemap', 'https://example.com', 'us',
                    'en', 'https://example.com/sitemap.xml', 10, 'sitemap',
                    'https://example.com/sitemap-news.xml')
            """)
        row = conn.execute("SELECT * FROM sources WHERE id = ?",
                           (cur.lastrowid,)).fetchone()
    return dict(row)


def test_sitemap_strategy_fetch_end_to_end(db_path, settings_obj):
    source = _insert_sitemap_source(db_path)
    routes = _routes_with_index() | {
        "https://example.com/news/story-one": httpx.Response(200, text=ARTICLE_HTML),
        "https://example.com/news/story-two": httpx.Response(200, text=ARTICLE_HTML),
    }
    factory = make_mock_client_factory(routes)
    result = run(fetch_source(db_path, source, settings_obj, factory,
                              robots=RobotsCache()))
    assert result["result"] == "ok"
    assert result["new_articles"] == 2  # lastmod 过旧的被过滤，未进管线
    with connect(db_path) as conn:
        urls = {r["url"] for r in conn.execute(
            "SELECT url FROM articles WHERE source_id = ?", (source["id"],))}
    assert urls == {"https://example.com/news/story-one",
                    "https://example.com/news/story-two"}

    # 第二轮：lastmod 不可信，URL 哈希去重兜底（设计 3.2 明确要求）
    result2 = run(fetch_source(db_path, source, settings_obj, factory,
                               robots=RobotsCache()))
    assert result2["result"] == "ok"
    assert result2["new_articles"] == 0
    assert result2["dedup_hits"] == 2
