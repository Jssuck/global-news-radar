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
