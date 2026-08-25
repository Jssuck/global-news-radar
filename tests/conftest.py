"""测试夹具：临时数据库 + 离线 mock HTTP（全部测试离线可跑）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 必须在导入 app 之前设置：测试用临时库 + 禁用后台轮询
os.environ["GNR_DB_PATH"] = "data/test-gnr.db"
os.environ["GNR_DISABLE_POLLER"] = "1"

import httpx

# ---- 离线样本（均为合成内容，不含任何真实新闻全文，符合合规红线）----

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Example News</title>
<item>
  <title>Sample Story One</title>
  <link>https://example.com/news/story-one?utm_source=feed#top</link>
  <pubDate>Mon, 01 Jun 2026 08:00:00 GMT</pubDate>
</item>
<item>
  <title>Sample Story Two</title>
  <link>https://example.com/news/story-two</link>
  <pubDate>Mon, 01 Jun 2026 07:00:00 GMT</pubDate>
</item>
</channel></rss>
"""

ARTICLE_HTML = """<!DOCTYPE html><html><head>
<title>Sample Story One</title>
<meta property="article:published_time" content="2026-06-01T08:00:00Z">
</head><body>
<article>
<h1>Sample Story One</h1>
<p>This is a synthetic sample article body used only for offline testing.
It contains enough English text for the extractor to treat it as the main
content of the page. No real news content is included in the test suite.</p>
<p>Second paragraph of the synthetic body, again purely illustrative text
written for fixture purposes and exercising the cleaning pipeline.</p>
</article>
</body></html>
"""

GEO_403_HTML = "<html><body>Sorry, this content is not available in your region.</body></html>"


def make_mock_client_factory(routes: dict[str, httpx.Response]):
    """构造注入 pipeline 的 httpx mock 工厂：按 URL 路径返回预置响应。"""

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        resp = routes.get(key) or routes.get(request.url.path)
        if resp is None:
            return httpx.Response(404, text="not found")
        # 支持条件 GET：响应带 etag 且请求携带相同 If-None-Match 时回 304
        etag = resp.headers.get("ETag")
        if etag and request.headers.get("If-None-Match") == etag:
            return httpx.Response(304)
        return resp

    def factory(timeout, user_agent) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


@pytest.fixture()
def db_path(tmp_path) -> str:
    """每个测试独立的临时 SQLite 库，并插入一条测试源。"""
    from app.db import init_db
    from app.sources_loader import load_seed_sources

    path = str(tmp_path / "test.db")
    init_db(path)
    load_seed_sources(path, REPO_ROOT / "sources")
    return path


@pytest.fixture()
def source(db_path) -> dict:
    from app.db import connect

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sources WHERE source_key = 'bbc-news'").fetchone()
    return dict(row)


@pytest.fixture()
def settings_obj():
    from app.config import get_settings

    return get_settings()
