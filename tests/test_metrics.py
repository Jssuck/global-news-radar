"""M1 metrics 导出测试：stats 端点新键 + compute_metrics 键完整性（全离线）。"""
import asyncio
import importlib.util
from pathlib import Path

import httpx

from app import pipeline
from app.db import connect
from app.metrics import compute_metrics
from app.pipeline import fetch_source
from app.robots import RobotsCache
from tests.conftest import ARTICLE_HTML, FEED_XML, make_mock_client_factory

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "collect_metrics", REPO_ROOT / "scripts/collect_metrics.py")
collect_metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect_metrics)

# gate_check.py 消费所需的 M1 验收键
REQUIRED_KEYS = {"sources_onboarded", "rss_discovery_rate", "extraction_success",
                 "coverage", "dedup_hits", "articles_total"}


def run(coro):
    return asyncio.run(coro)


def _routes_ok():
    return {
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(
            200, text=FEED_XML, headers={"ETag": '"v1"'}),
        "https://example.com/news/story-one": httpx.Response(200, text=ARTICLE_HTML),
        "https://example.com/news/story-two": httpx.Response(200, text=ARTICLE_HTML),
    }


def test_compute_metrics_keys_and_values(db_path, source, settings_obj):
    factory = make_mock_client_factory(_routes_ok())
    run(fetch_source(db_path, source, settings_obj, factory, robots=RobotsCache()))
    metrics = compute_metrics(db_path)
    assert REQUIRED_KEYS <= set(metrics)
    assert metrics["sources_onboarded"] >= 12
    assert metrics["articles_total"] == 2
    assert metrics["extraction_success"] == 1.0
    assert metrics["extraction_ok"] == 2 and metrics["extraction_total"] == 2
    assert metrics["rss_discovery_rate"] == 1.0
    assert metrics["rss_discovery_ok"] == 1 and metrics["rss_discovery_total"] == 1
    assert 0 < metrics["coverage"] <= 1.0
    assert metrics["dedup_hits"] == 0

    # 第二轮（换 ETag 避免 304）：URL 哈希去重兜底，dedup_hits 累计
    routes = _routes_ok()
    routes["https://feeds.bbci.co.uk/news/rss.xml"] = httpx.Response(
        200, text=FEED_XML, headers={"ETag": '"v2"'})
    factory2 = make_mock_client_factory(routes)
    src2 = dict(source)
    src2["etag"] = '"v1"'
    run(fetch_source(db_path, src2, settings_obj, factory2, robots=RobotsCache()))
    metrics2 = compute_metrics(db_path)
    assert metrics2["dedup_hits"] == 2
    assert metrics2["articles_total"] == 2  # 无重复摄入


def test_stats_endpoint_exposes_m1_metrics(client):
    sources = client.get("/api/v1/sources", params={"country": "gb"}).json()["items"]
    sid = sources[0]["id"]
    client.post(f"/api/v1/sources/{sid}/check")
    stats = client.get("/api/v1/stats").json()
    for key in ("extraction_success_rate", "rss_discovery_ok",
                "rss_discovery_total", "dedup_hits"):
        assert key in stats
    assert stats["extraction_success_rate"] == 1.0
    assert stats["rss_discovery_ok"] >= 1
    assert stats["dedup_hits"] == 0


def test_collect_metrics_cycle_offline(db_path, settings_obj, monkeypatch):
    """collect_metrics 的全量抓取循环可离线运行（MockTransport 注入）。"""
    monkeypatch.setattr(pipeline, "default_client_factory",
                        make_mock_client_factory(_routes_ok()))
    settings_obj.per_host_min_interval = 0  # 测试不限速
    # 只留 bbc 一个活跃源，其余源 mock 未覆盖会失败但不影响键完整性
    with connect(db_path) as conn:
        conn.execute("UPDATE sources SET active = 0 WHERE source_key != 'bbc-news'")
    results = run(collect_metrics.run_crawl_cycle(db_path, settings_obj))
    assert len(results) == 1 and results[0]["result"] == "ok"
    metrics = compute_metrics(db_path)
    assert REQUIRED_KEYS <= set(metrics)
    assert metrics["sources_active"] == 1
    assert metrics["rss_discovery_rate"] == 1.0
