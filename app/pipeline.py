"""抓取管线：发现（RSS / sitemap）→ robots 合规检查 → 条件 GET → 地域受限检测
（强/中信号 + 他国出口对照）→ 代理绑定路由 → 正文抽取兜底链 → 去重入库；
受限降级模式走 GDELT 聚合层（仅标题+URL 元数据）。

可测试性：所有网络访问经 client_factory 注入，测试用 httpx.MockTransport 即可
完全离线运行（见 tests/）；robots 缓存与 per-host 限速器亦可注入。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx

from . import sitemap
from .cleaner import detect_language, extract_article, normalize_url, url_hash
from .db import WRITE_LOCK, connect
from .gdelt import fetch_gdelt_articles, source_domain
from .geo import (
    GeoVerdict,
    confirmed_by_contrast,
    evaluate_response,
    evaluate_truncation,
)
from .llm import gate1_fail_reasons, llm_clean_article, provider_from_settings
from .proxyconf import alt_country_profile, proxy_url, resolve_proxy
from .renderer import budget_ok, render_fetch
from .robots import default_robots_cache

log = logging.getLogger("gnr.pipeline")

_TAG_RE = re.compile(r"<[^>]+>")

# 自适应轮询参数（设计 3.2.2：变了缩短、没变乘性退避拉长）
MIN_INTERVAL_MINUTES = 5    # 间隔下限
MAX_INTERVAL_MINUTES = 60   # 间隔上限
BACKOFF_FACTOR = 1.5        # 乘性伸缩系数
EMPTY_ROUNDS_THRESHOLD = 2  # 连续无新文章多少轮后拉长间隔


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_client_factory(timeout: float, user_agent: str,
                           proxy: str | None = None) -> httpx.AsyncClient:
    """默认 HTTP client 工厂；proxy 为 httpx 代理 URL（None=直连）。"""
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
        "headers": {"User-Agent": user_agent},
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


def adjust_interval(current_minutes: float, new_articles: int,
                    empty_streak: int) -> tuple[int, int]:
    """自适应轮询间隔（设计 3.2.2 第三级）：

    - 发现新文章：间隔 ÷1.5（下限 5 分钟），清空无新文章轮数；
    - 连续 2 轮无新文章：间隔 ×1.5（上限 60 分钟）；
    - 首轮无新文章：间隔不变，仅累计轮数。
    返回 (新间隔分钟, 新 empty_streak)。
    """
    if new_articles > 0:
        return max(MIN_INTERVAL_MINUTES,
                   round(current_minutes / BACKOFF_FACTOR)), 0
    streak = empty_streak + 1
    if streak >= EMPTY_ROUNDS_THRESHOLD:
        return min(MAX_INTERVAL_MINUTES,
                   round(current_minutes * BACKOFF_FACTOR)), streak
    return max(MIN_INTERVAL_MINUTES, round(current_minutes)), streak


def _entry_published(entry: Any) -> str | None:
    """从 feed entry 提取发布时间（feedparser 已做解析容错）。"""
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=UTC).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return None
    raw = getattr(entry, "published", None)
    if raw:
        try:
            return parsedate_to_datetime(raw).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return None
    return None


def _strip_tags(html_text: str) -> str:
    return _TAG_RE.sub("", html_text or "").strip()


def _feed_candidates(feed: Any) -> list[dict]:
    """把 feedparser entries 归一化为候选 dict（与 sitemap 候选同构）。"""
    return [
        {"link": getattr(entry, "link", None),
         "title": getattr(entry, "title", None),
         "summary": getattr(entry, "summary", None),
         "published": _entry_published(entry)}
        for entry in feed.entries
    ]


async def fetch_source(db_path: str, source: dict, settings,
                       client_factory=None, *, robots=None, limiter=None,
                       llm_provider=None) -> dict:
    """对单个源执行一次抓取，返回结果摘要 dict（同时写 fetch_log 与源状态）。

    client_factory 为 None 时在调用时解析默认工厂，测试可 monkeypatch
    app.pipeline.default_client_factory 注入 MockTransport 实现离线运行。
    robots/limiter 为可注入的 robots 缓存与 per-host 限速器（默认进程级共享）。

    M2 新增：三级代理绑定解析（源级>国家级>全局）注入抓取出口；
    geo_status=geo_restricted 且无代理绑定的源走 GDELT 聚合层降级模式。
    """
    if client_factory is None:
        client_factory = default_client_factory
    if robots is None:
        robots = default_robots_cache()
    # 质量门1判负时才进 LLM 二级清洗；provider 未配置则本级整体跳过
    if llm_provider is None:
        llm_provider = provider_from_settings(settings)
    now = utcnow()
    result: dict = {"source_id": source["id"], "result": "error", "new_articles": 0,
                    "http_status": None, "detail": None,
                    "extraction_ok": 0, "extraction_total": 0,
                    "dedup_hits": 0, "robots_skipped": 0}

    # 0a) 三级代理绑定解析：命中则本轮经代理出口抓取
    profile = resolve_proxy(db_path, source)
    bound_proxy = proxy_url(profile) if profile else None
    proxy_key = profile["profile_key"] if profile else None

    # 0b) 受限降级模式：已确认地域受限且无代理绑定 → 仅聚合层元数据
    if source.get("geo_status") == "geo_restricted" and not bound_proxy:
        return await _degraded_fetch(db_path, source, settings,
                                     client_factory, now, result)

    endpoint = source["feed_url"]  # rss 为 feed 地址；sitemap 策略为 sitemap/站点根地址

    async with client_factory(settings.request_timeout, settings.user_agent,
                              proxy=bound_proxy) as client:
        # 0c) robots.txt 合规检查（按 UA，域名级缓存 1 小时）
        if not await robots.allowed(client, endpoint, settings.user_agent):
            result["result"] = "robots_blocked"
            result["detail"] = f"robots.txt 禁止 UA 抓取: {endpoint}"
            _finalize(db_path, source, result, now, proxy_key=proxy_key)
            return result

        strategy = source.get("discovery_strategy") or "rss"
        if strategy == "sitemap":
            # sitemap 二级发现：lastmod 仅作提示，哈希去重兜底（设计 3.2）
            recent = await sitemap.discover_urls(
                client, source, limiter=limiter, robots=robots)
            candidates = [{"link": loc, "title": None, "summary": None,
                           "published": lm.isoformat(timespec="seconds") if lm else None}
                          for loc, lm in recent]
            outcome = await _process_candidates(
                db_path, client, source, candidates, result, now,
                settings, robots, limiter, llm_provider)
            if outcome == "ok":
                _set_geo(db_path, source["id"], "ok", None, None)
            _finalize(db_path, source, result, now, proxy_key=proxy_key)
            return result

        # ---- RSS 策略：条件 GET 抓 feed ----
        headers = {}
        if source.get("etag"):
            headers["If-None-Match"] = source["etag"]
        if source.get("last_modified"):
            headers["If-Modified-Since"] = source["last_modified"]
        try:
            if limiter is not None:
                await limiter.acquire(endpoint)
            resp = await client.get(endpoint, headers=headers)
        except httpx.HTTPError as exc:
            result["detail"] = f"网络错误: {exc.__class__.__name__}"
            _finalize(db_path, source, result, now, proxy_key=proxy_key)
            return result

        result["http_status"] = resp.status_code

        # 2) 地域受限 / 反爬信号判定（强/中信号分级 + 对照确认）
        verdict = evaluate_response(resp.status_code, resp.text,
                                    final_url=str(resp.url),
                                    request_url=endpoint)
        verdict = await _confirm_if_suspected(
            db_path, client_factory, settings, source, endpoint, verdict)
        if verdict.verdict == "geo_restricted":
            result["result"] = "geo_restricted"
            result["detail"] = verdict.reason
            region = (verdict.confirmed_region
                      or source.get("required_region") or source["country"])
            _set_geo(db_path, source["id"], "geo_restricted",
                     region, verdict.reason)
            _open_hint(db_path, source["id"], region,
                       verdict.reason, now, confirmed=True)
            _finalize(db_path, source, result, now, proxy_key=proxy_key)
            return result
        if verdict.verdict == "geo_suspected":
            result["result"] = "geo_suspected"
            result["detail"] = verdict.reason
            _set_geo(db_path, source["id"], "geo_suspected",
                     source["country"], verdict.reason)
            _open_hint(db_path, source["id"], source["country"],
                       verdict.reason, now, confirmed=False)
            _finalize(db_path, source, result, now, proxy_key=proxy_key)
            return result
        used_render = False
        if verdict.verdict == "anti_bot":
            # 渲染兜底：仅 anti_bot 判定且启用渲染、预算未耗尽时尝试
            rendered = await _try_rendered_feed(
                db_path, client_factory, settings, source, endpoint,
                bound_proxy)
            if rendered is not None:
                resp = rendered  # 复用下方 200/feed 解析路径
                used_render = True
            else:
                result["result"] = "anti_bot"
                result["detail"] = verdict.reason
                _finalize(db_path, source, result, now, proxy_key=proxy_key)
                return result

        # 3) 304 未变更：零成本跳过
        if resp.status_code == 304:
            result["result"] = "not_modified"
            _finalize(db_path, source, result, now, etag=source.get("etag"),
                      last_modified=source.get("last_modified"),
                      proxy_key=proxy_key)
            return result
        if resp.status_code != 200:
            result["detail"] = f"非预期状态码 {resp.status_code}"
            _finalize(db_path, source, result, now, proxy_key=proxy_key)
            return result

        # 4) 解析 feed，逐篇抓正文
        feed = feedparser.parse(resp.text)
        candidates = _feed_candidates(feed)
        outcome = await _process_candidates(
            db_path, client, source, candidates, result, now,
            settings, robots, limiter, llm_provider)
        if used_render and outcome == "ok":
            result["result"] = "rendered"  # 渲染兜底成功，预算计数口径

        # 5) 成功一轮：更新条件 GET 凭据，恢复 geo_status=ok
        if outcome == "ok":
            _set_geo(db_path, source["id"], "ok", None, None)
        _finalize(db_path, source, result, now,
                  etag=resp.headers.get("ETag") if outcome == "ok" else None,
                  last_modified=resp.headers.get("Last-Modified") if outcome == "ok" else None,
                  proxy_key=proxy_key)
        return result


async def _try_rendered_feed(db_path: str, client_factory, settings,
                             source: dict, endpoint: str,
                             bound_proxy: str | None):
    """渲染兜底：anti_bot 源经无头浏览器取回 feed 响应对象；失败返回 None。

    返回包装为带 .status_code/.text/.url 的轻量对象以复用后续解析路径。
    受 render_enabled 开关与每日预算双重约束（设计：渲染占比 ≤15%）。
    """
    if not (settings.render_enabled and budget_ok(db_path, settings)):
        return None
    html = await render_fetch(endpoint, settings, proxy=bound_proxy)
    if not html:
        return None

    class _RenderedResp:
        status_code = 200
        url = endpoint

        def __init__(self, text: str) -> None:
            self.text = text
            self.headers: dict = {}

    return _RenderedResp(html)


async def _confirm_if_suspected(db_path: str, client_factory, settings,
                                source: dict, url: str, verdict):
    """中信号对照验证：用一个他国代理出口重取同 URL，复现差异才确认（设计 3.3.1）。

    无可用的他国代理时保持 geo_suspected（提示用户补出口做确认）。
    """
    if verdict.verdict != "geo_suspected":
        return verdict
    alt = alt_country_profile(db_path, source.get("country"))
    if not alt:
        verdict.reason += "；无可用他国出口，待用户配置代理后对照确认"
        return verdict
    proxy = proxy_url(alt)
    if not proxy:
        return verdict
    try:
        async with client_factory(settings.request_timeout,
                                  settings.user_agent, proxy=proxy) as alt_client:
            resp = await alt_client.get(url)
        # 对照出口取到 200 且非空页面视为取得完整内容
        via_ok = resp.status_code == 200 and len(resp.text) > 200
    except httpx.HTTPError:
        via_ok = False
    confirmed, region, evidence = confirmed_by_contrast(
        direct_ok=False, via_proxy_ok=via_ok,
        proxy_country=alt.get("country"))
    if confirmed:
        return GeoVerdict("geo_restricted",
                          f"{verdict.reason}；{evidence}",
                          [*verdict.signals, "multi_egress_confirm"],
                          confirmed_region=region)
    verdict.reason += f"；{evidence}"
    return verdict


async def _degraded_fetch(db_path: str, source: dict, settings,
                          client_factory, now: str, result: dict) -> dict:
    """受限降级模式：仅经 GDELT 聚合层取标题+URL 元数据（设计 3.3.2）。

    不抓正文、不触达被封锁站点本身；degraded=1 标记入库。
    """
    async with client_factory(settings.request_timeout,
                              settings.user_agent) as client:
        items = await fetch_gdelt_articles(client, source_domain(source))
    inserted = 0
    for item in items:
        norm = normalize_url(item["url"])
        digest = url_hash(norm)
        with WRITE_LOCK, connect(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM articles WHERE url_hash = ?", (digest,)
            ).fetchone()
            if exists:
                result["dedup_hits"] += 1
                continue
            conn.execute(
                """
                INSERT INTO articles (source_id, url, url_hash, title, summary,
                                      body, language, published_at, fetched_at,
                                      degraded)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, 1)
                """,
                (source["id"], norm, digest, item.get("title") or "(无标题)",
                 (item.get("title") or "")[:200] or None,
                 item.get("language") or source["language"],
                 item.get("seendate"), now),
            )
            inserted += 1
    result["result"] = "degraded"
    result["new_articles"] = inserted
    result["detail"] = (f"受限降级：经 GDELT 聚合层取得 {len(items)} 条元数据"
                        f"（{inserted} 条新），正文不可用直至配置代理")
    _finalize(db_path, source, result, now)
    return result


async def _process_candidates(db_path: str, client: httpx.AsyncClient,
                              source: dict, candidates: list[dict],
                              result: dict, now: str, settings,
                              robots, limiter, llm_provider=None) -> str:
    """候选 URL 去重过滤 + 逐篇抓取入库；写回 result 计数，返回最终状态。"""
    new_count = 0
    for cand in candidates[: settings.max_articles_per_fetch]:
        link = cand.get("link")
        if not link:
            continue
        norm = normalize_url(link)
        digest = url_hash(norm)
        with connect(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM articles WHERE url_hash = ?", (digest,)
            ).fetchone()
        if exists:
            result["dedup_hits"] += 1  # lastmod 不可信，哈希去重兜底
            continue
        inserted = await _fetch_and_store_article(
            db_path, client, source, cand, norm, digest, now,
            settings, result, robots, limiter, llm_provider)
        if inserted == "geo_restricted":  # 正文页命中地域封锁 → 整源标记并中止
            result["result"] = "geo_restricted"
            result["detail"] = "文章页返回地域封锁响应"
            return "geo_restricted"
        if inserted == "robots_skipped":
            result["robots_skipped"] += 1
            continue
        new_count += 1 if inserted else 0
    result["result"] = "ok"
    result["new_articles"] = new_count
    if result["robots_skipped"]:
        result["detail"] = f"robots.txt 拦截 {result['robots_skipped']} 篇文章页"
    return "ok"


async def _fetch_and_store_article(db_path: str, client: httpx.AsyncClient,
                                   source: dict, cand: dict, norm_url: str,
                                   digest: str, now: str, settings,
                                   result: dict, robots, limiter,
                                   llm_provider=None) -> str | bool:
    """抓取单篇文章页、抽取正文并入库；返回 True/False/'geo_restricted'/'robots_skipped'。"""
    # 文章页同样过 robots 合规检查（域名级缓存，成本低）
    if not await robots.allowed(client, norm_url, settings.user_agent):
        return "robots_skipped"
    try:
        if limiter is not None:
            await limiter.acquire(norm_url)
        resp = await client.get(norm_url)
    except httpx.HTTPError:
        return False
    verdict = evaluate_response(resp.status_code, resp.text,
                                final_url=str(resp.url),
                                request_url=norm_url)
    if verdict.verdict == "geo_restricted":
        _set_geo(db_path, source["id"], "geo_restricted", source["country"],
                 f"文章页: {verdict.reason}")
        _open_hint(db_path, source["id"], source["country"],
                   f"文章页: {verdict.reason}", utcnow(), confirmed=True)
        return "geo_restricted"
    if resp.status_code != 200:
        return False

    meta = extract_article(resp.text, norm_url)
    raw_text = _strip_tags(resp.text)          # 供质量门/LLM 判定的页面原始文本
    detected_lang = detect_language(meta["body"] or raw_text,
                                    fallback=None)
    reasons = gate1_fail_reasons(meta, detected_lang, source["language"],
                                 len(raw_text))
    gate1_failed = bool(reasons)
    cleaned_by = meta["extractor"]

    if gate1_failed and llm_provider is not None:
        # 第二级 LLM 清洗：schema 闸门 + 确定性检查 + 重试/DLQ（设计 4.2）
        cleaned = await llm_clean_article(
            db_path, llm_provider, raw_text=raw_text, meta=meta,
            reasons=reasons, fetched_at=now,
            max_tokens=settings.llm_max_tokens)
        if cleaned is not None:
            if cleaned.is_ad_or_boilerplate:
                # 整体判定为非新闻 → 丢弃分支（设计 4.2.1）
                result["detail"] = "LLM 判定非新闻内容，丢弃"
                return False
            meta = {"title": cleaned.title, "body": cleaned.body,
                    "published_at": cleaned.published_at,
                    "authors": ", ".join(cleaned.authors) or None,
                    "extractor": meta["extractor"]}
            cleaned_by = "llm"
            detected_lang = cleaned.language

    # 抽取成功率埋点（M1 验收 M1-F2 / stats 导出）
    result["extraction_total"] += 1
    if meta["body"]:
        result["extraction_ok"] += 1
    title = meta["title"] or cand.get("title") or "(无标题)"
    body = meta["body"]
    # 摘要：正文前 200 字符；无正文时回退 feed summary（对外只输出短摘要，合规红线）
    feed_summary = _strip_tags(cand.get("summary") or "")
    summary = (body or feed_summary)[:200] or None
    published = meta["published_at"] or cand.get("published")
    language = detected_lang or source["language"]

    with WRITE_LOCK, connect(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO articles (source_id, url, url_hash, title, summary,
                                      body, language, published_at, fetched_at,
                                      gate1_failed, cleaned_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source["id"], norm_url, digest, title, summary, body,
                 language, published, now, int(gate1_failed), cleaned_by),
            )
        except sqlite3.IntegrityError:
            # url_hash 唯一约束冲突 = 并发重复摄入，按精确去重规则丢弃
            result["dedup_hits"] += 1
            return False

    # 中信号：正文截断检测（与该源历史正文长度对比，样本不足不判）
    if body:
        truncation = evaluate_truncation(
            len(body), _history_body_lens(db_path, source["id"]))
        if truncation:
            _set_geo(db_path, source["id"], "geo_suspected", source["country"],
                     truncation.reason)
            _open_hint(db_path, source["id"], source["country"],
                       truncation.reason, utcnow(), confirmed=False)
    return True


def _history_body_lens(db_path: str, source_id: int, limit: int = 20) -> list[int]:
    """该源近期入库文章的正文长度（截断检测的历史基线）。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT LENGTH(body) AS n FROM articles
            WHERE source_id = ? AND body IS NOT NULL AND degraded = 0
            ORDER BY id DESC LIMIT ?
            """,
            (source_id, limit),
        ).fetchall()
    return [r["n"] for r in rows]


def _set_geo(db_path: str, source_id: int, status: str,
             region: str | None, evidence: str | None) -> None:
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            "UPDATE sources SET geo_status=?, required_region=?, geo_evidence=? WHERE id=?",
            (status, region, evidence, source_id),
        )


def _open_hint(db_path: str, source_id: int, region: str | None,
               evidence: str, now: str, *, confirmed: bool) -> None:
    """打开地域受限提示（同源同状态只保留一条 open 提示，避免重复轰炸）。

    confirmed=False 为中信号未确认状态（hint 文案按 suspected 处理）。
    """
    status = "open" if confirmed else "suspected"
    with WRITE_LOCK, connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id, status FROM geo_hints WHERE source_id = ?"
            " AND status IN ('open','suspected')",
            (source_id,),
        ).fetchone()
        if existing:
            # 证据升级：suspected 轮转为 open 时更新证据与状态
            if confirmed and existing["status"] == "suspected":
                conn.execute(
                    "UPDATE geo_hints SET status='open', required_region=?,"
                    " evidence=? WHERE id=?",
                    (region, evidence, existing["id"]),
                )
            return
        conn.execute(
            "INSERT INTO geo_hints (source_id, required_region, evidence,"
            " status, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_id, region, evidence, status, now),
        )


def _finalize(db_path: str, source: dict, result: dict, now: str,
              etag: str | None = None, last_modified: str | None = None,
              proxy_key: str | None = None) -> None:
    """统一收尾：自适应间隔回写 + 更新源抓取状态 + 写 fetch_log。"""
    # 自适应轮询：仅在成功轮（ok/not_modified）调整，失败轮不动间隔
    interval = source.get("interval_minutes") or 10
    streak = source.get("empty_streak") or 0
    if result["result"] in ("ok", "not_modified"):
        interval, streak = adjust_interval(interval, result["new_articles"], streak)
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            """
            UPDATE sources SET last_fetched_at=?, last_status=?,
                   etag=COALESCE(?, etag), last_modified=COALESCE(?, last_modified),
                   interval_minutes=?, empty_streak=?
            WHERE id=?
            """,
            (now, result["http_status"], etag, last_modified,
             interval, streak, source["id"]),
        )
        conn.execute(
            """
            INSERT INTO fetch_log (source_id, fetched_at, http_status, result,
                                   new_articles, extraction_ok, extraction_total,
                                   dedup_hits, proxy_key, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source["id"], now, result["http_status"], result["result"],
             result["new_articles"], result["extraction_ok"],
             result["extraction_total"], result["dedup_hits"],
             proxy_key, result["detail"]),
        )
