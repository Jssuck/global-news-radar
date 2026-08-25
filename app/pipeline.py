"""抓取管线：发现（RSS / sitemap）→ robots 合规检查 → 条件 GET → 地域受限检测
→ 正文抽取兜底链 → 去重入库。

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
from .geo import evaluate_response
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


def default_client_factory(timeout: float, user_agent: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": user_agent},
    )


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
                       client_factory=None, *, robots=None, limiter=None) -> dict:
    """对单个源执行一次抓取，返回结果摘要 dict（同时写 fetch_log 与源状态）。

    client_factory 为 None 时在调用时解析默认工厂，测试可 monkeypatch
    app.pipeline.default_client_factory 注入 MockTransport 实现离线运行。
    robots/limiter 为可注入的 robots 缓存与 per-host 限速器（默认进程级共享）。
    """
    if client_factory is None:
        client_factory = default_client_factory
    if robots is None:
        robots = default_robots_cache()
    now = utcnow()
    result: dict = {"source_id": source["id"], "result": "error", "new_articles": 0,
                    "http_status": None, "detail": None,
                    "extraction_ok": 0, "extraction_total": 0,
                    "dedup_hits": 0, "robots_skipped": 0}
    strategy = source.get("discovery_strategy") or "rss"
    endpoint = source["feed_url"]  # rss 为 feed 地址；sitemap 策略为 sitemap/站点根地址

    async with client_factory(settings.request_timeout, settings.user_agent) as client:
        # 0) robots.txt 合规检查（按 UA，域名级缓存 1 小时）
        if not await robots.allowed(client, endpoint, settings.user_agent):
            result["result"] = "robots_blocked"
            result["detail"] = f"robots.txt 禁止 UA 抓取: {endpoint}"
            _finalize(db_path, source, result, now)
            return result

        if strategy == "sitemap":
            # sitemap 二级发现：lastmod 仅作提示，哈希去重兜底（设计 3.2）
            recent = await sitemap.discover_urls(
                client, source, limiter=limiter, robots=robots)
            candidates = [{"link": loc, "title": None, "summary": None,
                           "published": lm.isoformat(timespec="seconds") if lm else None}
                          for loc, lm in recent]
            outcome = await _process_candidates(
                db_path, client, source, candidates, result, now,
                settings, robots, limiter)
            if outcome == "ok":
                _set_geo(db_path, source["id"], "ok", None, None)
            _finalize(db_path, source, result, now)
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
            _finalize(db_path, source, result, now)
            return result

        result["http_status"] = resp.status_code

        # 2) 地域受限 / 反爬信号判定（geo_verdict 移植逻辑）
        verdict = evaluate_response(resp.status_code, resp.text)
        if verdict.verdict == "geo_restricted":
            result["result"] = "geo_restricted"
            result["detail"] = verdict.reason
            # MVP 简化：required_region 取源所属国家（设计 3.3.1 的对照实验反推留待 v1.0）
            _set_geo(db_path, source["id"], "geo_restricted", source["country"], verdict.reason)
            _finalize(db_path, source, result, now)
            return result
        if verdict.verdict == "anti_bot":
            result["result"] = "anti_bot"
            result["detail"] = verdict.reason
            _finalize(db_path, source, result, now)
            return result

        # 3) 304 未变更：零成本跳过
        if resp.status_code == 304:
            result["result"] = "not_modified"
            _finalize(db_path, source, result, now, etag=source.get("etag"),
                      last_modified=source.get("last_modified"))
            return result
        if resp.status_code != 200:
            result["detail"] = f"非预期状态码 {resp.status_code}"
            _finalize(db_path, source, result, now)
            return result

        # 4) 解析 feed，逐篇抓正文
        feed = feedparser.parse(resp.text)
        candidates = _feed_candidates(feed)
        outcome = await _process_candidates(
            db_path, client, source, candidates, result, now,
            settings, robots, limiter)

        # 5) 成功一轮：更新条件 GET 凭据，恢复 geo_status=ok
        if outcome == "ok":
            _set_geo(db_path, source["id"], "ok", None, None)
        _finalize(db_path, source, result, now,
                  etag=resp.headers.get("ETag") if outcome == "ok" else None,
                  last_modified=resp.headers.get("Last-Modified") if outcome == "ok" else None)
        return result


async def _process_candidates(db_path: str, client: httpx.AsyncClient,
                              source: dict, candidates: list[dict],
                              result: dict, now: str, settings,
                              robots, limiter) -> str:
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
            settings, result, robots, limiter)
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
                                   result: dict, robots, limiter) -> str | bool:
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
    verdict = evaluate_response(resp.status_code, resp.text)
    if verdict.verdict == "geo_restricted":
        _set_geo(db_path, source["id"], "geo_restricted", source["country"],
                 f"文章页: {verdict.reason}")
        return "geo_restricted"
    if resp.status_code != 200:
        return False

    meta = extract_article(resp.text, norm_url)
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
    language = detect_language(body or feed_summary, fallback=source["language"])

    with WRITE_LOCK, connect(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO articles (source_id, url, url_hash, title, summary,
                                      body, language, published_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source["id"], norm_url, digest, title, summary, body,
                 language, published, now),
            )
        except sqlite3.IntegrityError:
            # url_hash 唯一约束冲突 = 并发重复摄入，按精确去重规则丢弃
            result["dedup_hits"] += 1
            return False
    return True


def _set_geo(db_path: str, source_id: int, status: str,
             region: str | None, evidence: str | None) -> None:
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            "UPDATE sources SET geo_status=?, required_region=?, geo_evidence=? WHERE id=?",
            (status, region, evidence, source_id),
        )


def _finalize(db_path: str, source: dict, result: dict, now: str,
              etag: str | None = None, last_modified: str | None = None) -> None:
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
                                   dedup_hits, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source["id"], now, result["http_status"], result["result"],
             result["new_articles"], result["extraction_ok"],
             result["extraction_total"], result["dedup_hits"], result["detail"]),
        )
