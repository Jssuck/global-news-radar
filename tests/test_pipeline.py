"""抓取管线测试：MockTransport 模拟 feed/文章页/地域封锁响应，全部离线。"""
import asyncio

import httpx

from app.db import connect
from app.pipeline import fetch_source
from tests.conftest import (
    ARTICLE_HTML,
    FEED_XML,
    GEO_403_HTML,
    make_mock_client_factory,
)


def run(coro):
    return asyncio.run(coro)


def _routes_ok():
    return {
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(
            200, text=FEED_XML, headers={"ETag": '"v1"'}),
        "https://example.com/news/story-one": httpx.Response(200, text=ARTICLE_HTML),
        "https://example.com/news/story-two": httpx.Response(200, text=ARTICLE_HTML),
    }


def test_fetch_source_inserts_articles(db_path, source, settings_obj):
    factory = make_mock_client_factory(_routes_ok())
    result = run(fetch_source(db_path, source, settings_obj, factory))
    assert result["result"] == "ok"
    assert result["new_articles"] == 2
    with connect(db_path) as conn:
        articles = conn.execute(
            "SELECT * FROM articles WHERE source_id = ?", (source["id"],)).fetchall()
        src = conn.execute("SELECT * FROM sources WHERE id = ?",
                           (source["id"],)).fetchone()
        logs = conn.execute("SELECT * FROM fetch_log WHERE source_id = ?",
                            (source["id"],)).fetchall()
    assert len(articles) == 2
    # URL 已规范化（去 utm/fragment），摘要截断在 200 字符内
    urls = {a["url"] for a in articles}
    assert "https://example.com/news/story-one" in urls
    assert all(len(a["summary"] or "") <= 200 for a in articles)
    # 条件 GET 凭据与 geo_status 已写回
    assert src["etag"] == '"v1"' and src["geo_status"] == "ok"
    assert len(logs) == 1 and logs[0]["result"] == "ok"


def test_second_fetch_dedups_and_304(db_path, source, settings_obj):
    factory = make_mock_client_factory(_routes_ok())
    run(fetch_source(db_path, source, settings_obj, factory))
    # 第二次：feed 返回相同 ETag → mock 命中 304，文章零新增
    src2 = dict(source)
    src2["etag"] = '"v1"'
    result = run(fetch_source(db_path, src2, settings_obj, factory))
    assert result["result"] == "not_modified"
    with connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert n == 2  # 无重复入库


def test_feed_451_marks_source_geo_restricted(db_path, source, settings_obj):
    routes = {"https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(451)}
    factory = make_mock_client_factory(routes)
    result = run(fetch_source(db_path, source, settings_obj, factory))
    assert result["result"] == "geo_restricted"
    with connect(db_path) as conn:
        src = conn.execute("SELECT * FROM sources WHERE id = ?",
                           (source["id"],)).fetchone()
    assert src["geo_status"] == "geo_restricted"
    assert src["required_region"] == "gb"  # MVP 简化：required_region 取源所属国
    assert "451" in src["geo_evidence"]


def test_article_page_geo_block_marks_source(db_path, source, settings_obj):
    routes = {
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(200, text=FEED_XML),
        "https://example.com/news/story-one": httpx.Response(403, text=GEO_403_HTML),
        "https://example.com/news/story-two": httpx.Response(403, text=GEO_403_HTML),
    }
    factory = make_mock_client_factory(routes)
    result = run(fetch_source(db_path, source, settings_obj, factory))
    assert result["result"] == "geo_restricted"
    with connect(db_path) as conn:
        src = conn.execute("SELECT * FROM sources WHERE id = ?",
                           (source["id"],)).fetchone()
        n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert src["geo_status"] == "geo_restricted"
    assert n == 0


def test_429_does_not_change_geo_status(db_path, source, settings_obj):
    routes = {"https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(429)}
    factory = make_mock_client_factory(routes)
    result = run(fetch_source(db_path, source, settings_obj, factory))
    assert result["result"] == "anti_bot"
    with connect(db_path) as conn:
        src = conn.execute("SELECT * FROM sources WHERE id = ?",
                           (source["id"],)).fetchone()
    assert src["geo_status"] != "geo_restricted"  # 限流不误判为地域封锁
