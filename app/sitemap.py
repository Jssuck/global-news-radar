"""sitemap 二级发现（M1，设计 3.2 降级链第二级）。

当 source 的 discovery.strategy=sitemap 时：
1. 确定 sitemap 入口：优先 source.yaml 的 discovery.sitemap_url；
   否则解析 robots.txt 的 Sitemap 指令；再否则探测 <base>/sitemap.xml；
2. 支持 sitemap index 嵌套（递归解析子 sitemap，限制深度与总量）；
3. 按 <lastmod> 过滤近 48 小时的 URL 进管线——lastmod 只作提示
   （CMS 重部署常重盖时间戳），无 lastmod 的条目一律放行，
   最终由 URL 规范化 + SHA-256 内容哈希去重兜底。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree

import httpx

log = logging.getLogger("gnr.sitemap")

# 近 48 小时窗口（lastmod 过滤提示）
RECENT_WINDOW = timedelta(hours=48)
# 单源最多解析的 sitemap 文件数 / 嵌套深度（防失控）
MAX_SITEMAPS = 20
MAX_DEPTH = 3


def parse_lastmod(raw: str | None) -> datetime | None:
    """解析 <lastmod>（ISO 8601 变体），失败返回 None。"""
    if not raw:
        return None
    raw = raw.strip()
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    try:  # 仅日期
        return datetime.fromisoformat(raw[:10]).replace(tzinfo=UTC)
    except ValueError:
        return None


def _text(elem: ElementTree.Element | None) -> str | None:
    return elem.text.strip() if elem is not None and elem.text else None


def parse_sitemap(xml_text: str) -> dict:
    """解析 sitemap XML（命名空间无关），返回 {type, sitemaps, urls}。

    type=index 时 sitemaps 为 [(loc, lastmod)]；type=urlset 时 urls 为
    [(loc, lastmod)]；解析失败抛 ValueError 由调用方降级。
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"sitemap XML 解析失败: {exc}") from exc
    tag = root.tag.rsplit("}", 1)[-1].lower()
    result: dict = {"type": tag, "sitemaps": [], "urls": []}
    for child in root:
        loc = lastmod = None
        for field in child:
            name = field.tag.rsplit("}", 1)[-1].lower()
            if name == "loc":
                loc = _text(field)
            elif name == "lastmod":
                lastmod = _text(field)
        if not loc:
            continue
        entry = (loc, parse_lastmod(lastmod))
        if tag == "sitemapindex":
            result["sitemaps"].append(entry)
        else:
            result["urls"].append(entry)
    return result


def filter_recent(urls: list[tuple[str, datetime | None]],
                  now: datetime | None = None,
                  window: timedelta = RECENT_WINDOW) -> list[tuple[str, datetime | None]]:
    """按 lastmod 过滤近 window 的 URL；无 lastmod 的放行（哈希去重兜底）。"""
    now = now or datetime.now(UTC)
    cutoff = now - window
    return [(loc, lm) for loc, lm in urls if lm is None or lm >= cutoff]


async def _fetch_sitemap(client: httpx.AsyncClient, url: str, limiter=None) -> str | None:
    """抓 sitemap 文件，失败返回 None（由调用方降级处理）。"""
    try:
        if limiter is not None:
            await limiter.acquire(url)
        resp = await client.get(url)
    except httpx.HTTPError as exc:
        log.info("sitemap 抓取失败 %s: %s", url, exc.__class__.__name__)
        return None
    if resp.status_code != 200:
        log.info("sitemap 非 200 响应 %s: %s", url, resp.status_code)
        return None
    return resp.text


async def discover_urls(client: httpx.AsyncClient, source: dict,
                        *, limiter=None, robots=None,
                        now: datetime | None = None) -> list[tuple[str, datetime | None]]:
    """sitemap 二级发现主入口：返回近 48 小时候选 (url, lastmod) 列表。

    去重（按 URL 规范化哈希）由管线统一处理；此处只按 lastmod 提示过滤。
    """
    base_url = (source.get("base_url") or "").rstrip("/")
    # 1) 入口发现：显式 sitemap_url → robots.txt Sitemap 指令 → 默认路径探测
    seeds: list[str] = []
    if source.get("sitemap_url"):
        seeds.append(source["sitemap_url"])
    elif robots is not None and base_url:
        seeds.extend(await robots.sitemaps(client, base_url))
    if not seeds and base_url:
        seeds.append(f"{base_url}/sitemap.xml")

    now = now or datetime.now(UTC)
    found: list[tuple[str, datetime | None]] = []
    # (url, depth) 队列，广度优先展开 sitemap index 嵌套
    queue: list[tuple[str, int]] = [(u, 0) for u in seeds]
    seen: set[str] = set()
    fetched = 0
    while queue and fetched < MAX_SITEMAPS:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        text = await _fetch_sitemap(client, url, limiter)
        if text is None:
            continue
        fetched += 1
        try:
            parsed = parse_sitemap(text)
        except ValueError as exc:
            log.info("%s", exc)
            continue
        if parsed["type"] == "sitemapindex" and depth < MAX_DEPTH:
            queue.extend((loc, depth + 1) for loc, _ in parsed["sitemaps"])
        else:
            found.extend(parsed["urls"])

    recent = filter_recent(found, now)
    log.info("sitemap 发现 %s: 候选 %d 条，近 48h %d 条",
             source.get("source_key"), len(found), len(recent))
    return recent
