"""M2a 代理三级绑定解析与受限降级模式测试（全部离线，MockTransport）。"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.conftest import ARTICLE_HTML, FEED_XML


def run(coro):
    return asyncio.run(coro)


def _mk_profile(db_path, key="us-dc", country="us", ptype="datacenter",
                endpoint="http://10.0.0.1:8080"):
    from app.proxyconf import create_profile
    return create_profile(db_path, {
        "profile_key": key, "type": ptype, "country": country,
        "endpoint": endpoint, "credentials_env": None,
        "provider": "test", "notes": None}, "2026-09-11T00:00:00+00:00")


def _mk_binding(db_path, scope, profile_id, source_id=None,
                country_code=None, priority=100):
    from app.proxyconf import create_binding
    return create_binding(db_path, {
        "scope": scope, "source_id": source_id,
        "country_code": country_code, "proxy_profile_id": profile_id,
        "priority": priority}, "2026-09-11T00:00:00+00:00")


def test_resolve_three_levels(db_path, source):
    """源级 > 国家级 > 全局；同级取 priority 最小者。"""
    from app.proxyconf import resolve_proxy

    p_us = _mk_profile(db_path, "us-dc", "us")
    p_gb = _mk_profile(db_path, "gb-dc", "gb")
    p_jp = _mk_profile(db_path, "jp-dc", "jp")

    # 只有全局绑定 → 全局生效
    _mk_binding(db_path, "global", p_jp["id"], priority=50)
    assert resolve_proxy(db_path, source)["profile_key"] == "jp-dc"

    # 国家级覆盖全局（bbc-news country=gb），同级取 priority 小者
    _mk_binding(db_path, "country", p_us["id"], country_code="gb", priority=10)
    _mk_binding(db_path, "country", p_gb["id"], country_code="gb", priority=5)
    assert resolve_proxy(db_path, source)["profile_key"] == "gb-dc"

    # 源级覆盖国家级
    _mk_binding(db_path, "source", p_us["id"], source_id=source["id"], priority=1)
    assert resolve_proxy(db_path, source)["profile_key"] == "us-dc"


def test_resolve_no_binding(db_path, source):
    from app.proxyconf import resolve_proxy
    assert resolve_proxy(db_path, source) is None


def test_binding_validation(db_path):
    """scope 必填字段校验：source 需 source_id，country 需 alpha-2 码。"""
    from app.proxyconf import create_binding
    p = _mk_profile(db_path)
    with pytest.raises(ValueError, match="source_id"):
        create_binding(db_path, {"scope": "source", "proxy_profile_id": p["id"],
                                 "priority": 1}, "2026-09-11T00:00:00+00:00")
    with pytest.raises(ValueError, match="country_code"):
        create_binding(db_path, {"scope": "country",
                                 "proxy_profile_id": p["id"],
                                 "country_code": "usa", "priority": 1},
                       "2026-09-11T00:00:00+00:00")
    with pytest.raises(ValueError, match="不存在"):
        create_binding(db_path, {"scope": "global", "proxy_profile_id": 9999,
                                 "priority": 1}, "2026-09-11T00:00:00+00:00")


def test_proxy_url_credentials(db_path, monkeypatch):
    """凭据经 credentials_env 注入 URL userinfo，不落明文。"""
    from app.proxyconf import proxy_url
    monkeypatch.setenv("GNR_TEST_CRED", "user:pass")
    p = _mk_profile(db_path, endpoint="http://proxy.local:9000")
    p["credentials_env"] = "GNR_TEST_CRED"
    assert proxy_url(p) == "http://user:pass@proxy.local:9000/"
    p["credentials_env"] = "GNR_MISSING_ENV"
    assert proxy_url(p) == "http://proxy.local:9000"


def test_fetch_uses_bound_proxy(db_path, source, settings_obj):
    """命中绑定的源经代理出口抓取，fetch_log 记录 proxy_key。"""
    from app.db import connect
    from app.pipeline import fetch_source

    p = _mk_profile(db_path, "uk-exit", "gb")
    _mk_binding(db_path, "source", p["id"], source_id=source["id"], priority=1)

    seen = {}
    routes = {
        "https://feeds.bbci.co.uk/robots.txt": httpx.Response(
            200, text="User-agent: *\nAllow: /\n"),
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(200, text=FEED_XML),
        "https://example.com/robots.txt": httpx.Response(
            200, text="User-agent: *\nAllow: /\n"),
        "https://example.com/news/story-one": httpx.Response(200, text=ARTICLE_HTML),
        "https://example.com/news/story-two": httpx.Response(200, text=ARTICLE_HTML),
    }

    def factory(timeout, user_agent, proxy=None):
        seen["proxy"] = proxy
        return httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: routes.get(str(req.url)) or routes.get(req.url.path)
            or httpx.Response(404)))

    result = run(fetch_source(db_path, source, settings_obj,
                              client_factory=factory))
    assert result["result"] == "ok"
    assert seen["proxy"] == "http://10.0.0.1:8080"
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT proxy_key FROM fetch_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["proxy_key"] == "uk-exit"


def test_geo_restricted_degraded_to_gdelt(db_path, source, settings_obj):
    """已确认受限且无代理 → GDELT 聚合层降级：只入标题+URL，degraded=1。"""
    from app.db import WRITE_LOCK, connect
    from app.pipeline import fetch_source

    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute("UPDATE sources SET geo_status='geo_restricted',"
                     " required_region='gb' WHERE id=?", (source["id"],))
        source = dict(conn.execute("SELECT * FROM sources WHERE id=?",
                                   (source["id"],)).fetchone())

    gdelt_body = {"articles": [
        {"url": "https://www.bbc.com/news/articles/xyz",
         "title": "Aggregated Story", "seendate": "20260911T010000Z",
         "language": "English", "sourcecountry": "United Kingdom"}]}

    def factory(timeout, user_agent, proxy=None):
        def handler(req):
            if "gdeltproject.org" in str(req.url):
                return httpx.Response(200, json=gdelt_body)
            return httpx.Response(404)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = run(fetch_source(db_path, source, settings_obj,
                              client_factory=factory))
    assert result["result"] == "degraded"
    assert result["new_articles"] == 1
    with connect(db_path) as conn:
        art = conn.execute(
            "SELECT title, body, degraded FROM articles WHERE source_id=?",
            (source["id"],)).fetchone()
    assert art["title"] == "Aggregated Story"
    assert art["body"] is None and art["degraded"] == 1


def test_geo_suspected_contrast_confirms(db_path, source, settings_obj):
    """中信号（地域重定向）+ 他国出口对照取到内容 → 确认 geo_restricted，
    required_region 反推为对照出口国。"""
    from app.db import connect
    from app.pipeline import fetch_source

    # 对照出口只要求存在可用 profile（alt_country_profile 不依赖绑定）
    _mk_profile(db_path, "jp-exit", "jp")

    def factory(timeout, user_agent, proxy=None):
        def handler(req):
            url = str(req.url)
            if url.endswith("/robots.txt"):
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            if "geo-block" in url:  # 地域警告页本身返回 200
                return httpx.Response(
                    200, text="<html><title>Geo restriction</title></html>")
            if proxy:  # 对照出口取到正常 feed
                return httpx.Response(200, text=FEED_XML)
            # 直连：重定向至地域警告页
            return httpx.Response(
                301, headers={"Location":
                              "https://feeds.bbci.co.uk/geo-block/notice"})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 follow_redirects=True)

    result = run(fetch_source(db_path, source, settings_obj,
                              client_factory=factory))
    assert result["result"] == "geo_restricted"
    with connect(db_path) as conn:
        src = conn.execute("SELECT geo_status, required_region FROM sources"
                           " WHERE id=?", (source["id"],)).fetchone()
        hint = conn.execute("SELECT status, required_region FROM geo_hints"
                            " WHERE source_id=?", (source["id"],)).fetchone()
    assert src["geo_status"] == "geo_restricted"
    assert src["required_region"] == "jp"      # 对照出口国反推
    assert hint["status"] == "open"


def test_geo_suspected_no_egress_stays_suspected(db_path, source,
                                                 settings_obj):
    """中信号且无他国出口 → geo_suspected + suspected 提示（不确认）。"""
    from app.db import connect
    from app.pipeline import fetch_source

    def factory(timeout, user_agent, proxy=None):
        def handler(req):
            url = str(req.url)
            if url.endswith("/robots.txt"):
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            if "geo-blocked" in url:
                return httpx.Response(
                    200, text="<html><title>Region notice</title></html>")
            return httpx.Response(
                301, headers={"Location":
                              "https://feeds.bbci.co.uk/geo-blocked"})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 follow_redirects=True)

    result = run(fetch_source(db_path, source, settings_obj,
                              client_factory=factory))
    assert result["result"] == "geo_suspected"
    with connect(db_path) as conn:
        src = conn.execute("SELECT geo_status FROM sources WHERE id=?",
                           (source["id"],)).fetchone()
        hint = conn.execute("SELECT status FROM geo_hints WHERE source_id=?",
                            (source["id"],)).fetchone()
    assert src["geo_status"] == "geo_suspected"
    assert hint["status"] == "suspected"


def test_sync_proxy_config_from_yaml(db_path, source, tmp_path):
    """proxies.yaml 导入 DB：profiles upsert + source-scope 绑定解析。"""
    from app.proxyconf import resolve_proxy, sync_proxy_config
    cfg = tmp_path / "proxies.yaml"
    cfg.write_text(
        "profiles:\n"
        "  - profile_id: gb-exit\n"
        "    type: datacenter\n"
        "    country: gb\n"
        "    endpoint: http://10.1.1.1:3128\n"
        "bindings:\n"
        "  - scope: source\n"
        "    source: bbc-news\n"
        "    profile_id: gb-exit\n"
        "    priority: 10\n",
        encoding="utf-8")
    n = sync_proxy_config(db_path, str(cfg), "2026-09-11T00:00:00+00:00")
    assert n == 1
    assert resolve_proxy(db_path, source)["profile_key"] == "gb-exit"
