"""API 与页面冒烟测试（TestClient，离线）。

check 端点经 monkeypatch 注入 MockTransport，不发真实网络请求。
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.db import connect, init_db
from app.main import app
from tests.conftest import ARTICLE_HTML, FEED_XML, make_mock_client_factory


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 指向独立临时库，禁用轮询在 conftest 中已设置
    monkeypatch.setenv("GNR_DB_PATH", str(tmp_path / "api-test.db"))
    init_db(str(tmp_path / "api-test.db"))
    # check 端点的网络访问改为 mock
    monkeypatch.setattr(pipeline, "default_client_factory",
                        make_mock_client_factory({
                            "https://feeds.bbci.co.uk/news/rss.xml":
                                httpx.Response(200, text=FEED_XML),
                            "https://example.com/news/story-one":
                                httpx.Response(200, text=ARTICLE_HTML),
                            "https://example.com/news/story-two":
                                httpx.Response(200, text=ARTICLE_HTML),
                        }))
    with TestClient(app) as c:
        yield c


def test_sources_endpoint(client):
    resp = client.get("/api/v1/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 12
    item = data["items"][0]
    assert "geo_status" in item and "hint" in item
    # 国家过滤
    gb = client.get("/api/v1/sources", params={"country": "gb"}).json()
    assert gb["total"] >= 1 and all(i["country"] == "gb" for i in gb["items"])


def test_check_endpoint_fetches_articles(client):
    sources = client.get("/api/v1/sources", params={"country": "gb"}).json()["items"]
    sid = sources[0]["id"]
    resp = client.post(f"/api/v1/sources/{sid}/check")
    assert resp.status_code == 200
    assert resp.json()["result"] == "ok"

    listing = client.get("/api/v1/articles").json()
    assert listing["total"] == 2
    article = listing["items"][0]
    assert "body" not in article  # 合规：API 不返回全文
    assert article["url"].startswith("https://example.com/news/story")

    detail = client.get(f"/api/v1/articles/{article['id']}")
    assert detail.status_code == 200
    # q 过滤
    assert client.get("/api/v1/articles", params={"q": "Sample"}).json()["total"] == 2
    assert client.get("/api/v1/articles", params={"q": "不存在"}).json()["total"] == 0
    # 404
    assert client.get("/api/v1/articles/9999").status_code == 404
    assert client.post("/api/v1/sources/9999/check").status_code == 404


def test_stats_and_geo_hint_flow(client):
    sources = client.get("/api/v1/sources", params={"country": "gb"}).json()["items"]
    sid = sources[0]["id"]
    client.post(f"/api/v1/sources/{sid}/check")
    stats = client.get("/api/v1/stats").json()
    assert stats["sources_total"] >= 12
    assert stats["articles_total"] == 2
    assert stats["geo_restricted_sources"] == 0
    assert stats["last_fetch_at"] is not None

    # 手工把源置为受限，验证 API 输出 hint 文案
    from app.config import get_settings
    with connect(get_settings().db_path) as conn:
        conn.execute(
            "UPDATE sources SET geo_status='geo_restricted', required_region='us',"
            " geo_evidence='HTTP 451' WHERE id=?", (sid,))
    data = client.get("/api/v1/sources", params={"geo_status": "geo_restricted"}).json()
    assert data["total"] == 1
    hint = data["items"][0]["hint"]
    assert "美国" in hint and "代理" in hint
    assert client.get("/api/v1/stats").json()["geo_restricted_sources"] == 1


def test_pages_render(client):
    index = client.get("/")
    assert index.status_code == 200 and "新闻" in index.text
    board = client.get("/sources")
    assert board.status_code == 200
    assert "代理" in board.text and "profiles" in board.text
