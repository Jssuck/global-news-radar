"""GDELT 聚合层兜底（设计 3.2.1 第四级 + 3.3.2 受限降级模式）。

地域受限且未配置代理的源不「静默断更」：经 GDELT DOC 2.0 API 按域名过滤
取回标题 + URL 级元数据入库（articles.degraded=1，不抓正文——不触碰被封锁
站点本身，同时满足「不分发全文」红线）。

DOC 2.0 API 免密钥；限速约 1 req/5s，由调用方控制频率。
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("gnr.gdelt")

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


def source_domain(source: dict) -> str:
    """取源的发现端点域名（feed_url/sitemap_url 退化到 base_url）。"""
    for key in ("feed_url", "sitemap_url", "base_url"):
        raw = source.get(key)
        if raw:
            host = urlsplit(raw).hostname
            if host:
                return host.lower().removeprefix("www.")
    return ""


async def fetch_gdelt_articles(client: httpx.AsyncClient, domain: str,
                               *, timespan: str = "24h",
                               maxrecords: int = 50) -> list[dict]:
    """按 domain 过滤取 GDELT 最近文章列表。

    返回 [{url, title, seendate, language, sourcecountry}]；请求失败返回 []。
    """
    if not domain:
        return []
    try:
        resp = await client.get(GDELT_DOC_API, params={
            "query": f"domain:{domain}",
            "mode": "artlist",
            "maxrecords": str(maxrecords),
            "format": "json",
            "timespan": timespan,
        })
    except httpx.HTTPError as exc:
        log.info("GDELT 请求失败 %s: %s", domain, exc.__class__.__name__)
        return []
    if resp.status_code != 200:
        log.info("GDELT 返回 %s: %s", resp.status_code, domain)
        return []
    try:
        articles = resp.json().get("articles") or []
    except ValueError:
        return []
    return [
        {"url": a.get("url"), "title": a.get("title"),
         "seendate": a.get("seendate"), "language": a.get("language"),
         "sourcecountry": a.get("sourcecountry")}
        for a in articles if a.get("url")
    ]


async def daily_gdelt_check(db_path: str, settings,
                            client_factory) -> dict:
    """漏抓对照（M3-F4）：geo_restricted 源每日比对 GDELT 命中 vs 本地收录。

    GDELT 限速约 1 req/5s；checked_at 按 UTC 日去重，当日已跑的源跳过。
    """
    import asyncio

    from .db import WRITE_LOCK, connect
    from .pipeline import utcnow

    today = utcnow()[:10]
    with connect(db_path) as conn:
        restricted = conn.execute(
            "SELECT * FROM sources WHERE geo_status='geo_restricted'"
        ).fetchall()
        done = {r["source_id"] for r in conn.execute(
            "SELECT source_id FROM gdelt_checks WHERE checked_at=?",
            (today,))}
        todo = [dict(s) for s in restricted if s["id"] not in done]

    results = {}
    async with client_factory(settings.request_timeout,
                              settings.user_agent) as client:
        for src in todo:
            hits = await fetch_gdelt_articles(
                client, source_domain(src), timespan=settings.gdelt_timespan)
            with connect(db_path) as conn:
                local = conn.execute(
                    "SELECT COUNT(*) FROM articles WHERE source_id=?"
                    " AND fetched_at >= datetime('now', '-1 day')",
                    (src["id"],)).fetchone()[0]
            with WRITE_LOCK, connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO gdelt_checks (source_id, checked_at,"
                    " gdelt_hits, local_count) VALUES (?,?,?,?)",
                    (src["id"], today, len(hits), local))
            results[src["source_key"]] = {"gdelt_hits": len(hits),
                                          "local": local}
            await asyncio.sleep(5)  # GDELT 1req/5s 礼貌限速
    return results
