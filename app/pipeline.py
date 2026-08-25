"""抓取管线：RSS 轮询 → 条件 GET → 地域受限检测 → 正文抽取 → 去重入库。

可测试性：所有网络访问经 client_factory 注入，测试用 httpx.MockTransport 即可
完全离线运行（见 tests/）。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx

from .cleaner import detect_language, extract_article, normalize_url, url_hash
from .db import WRITE_LOCK, connect
from .geo import evaluate_response

_TAG_RE = re.compile(r"<[^>]+>")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_client_factory(timeout: float, user_agent: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": user_agent},
    )


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


async def fetch_source(db_path: str, source: dict, settings,
                       client_factory=None) -> dict:
    """对单个源执行一次抓取，返回结果摘要 dict（同时写 fetch_log 与源状态）。

    client_factory 为 None 时在调用时解析默认工厂，测试可 monkeypatch
    app.pipeline.default_client_factory 注入 MockTransport 实现离线运行。
    """
    if client_factory is None:
        client_factory = default_client_factory
    now = utcnow()
    result: dict = {"source_id": source["id"], "result": "error", "new_articles": 0,
                    "http_status": None, "detail": None}
    headers = {}
    if source.get("etag"):
        headers["If-None-Match"] = source["etag"]
    if source.get("last_modified"):
        headers["If-Modified-Since"] = source["last_modified"]

    async with client_factory(settings.request_timeout, settings.user_agent) as client:
        # 1) 条件 GET 抓 feed
        try:
            resp = await client.get(source["feed_url"], headers=headers)
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
        new_count = 0
        for entry in feed.entries[: settings.max_articles_per_fetch]:
            link = getattr(entry, "link", None)
            if not link:
                continue
            norm = normalize_url(link)
            digest = url_hash(norm)
            with connect(db_path) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM articles WHERE url_hash = ?", (digest,)
                ).fetchone()
            if exists:
                continue
            inserted = await _fetch_and_store_article(
                db_path, client, source, entry, norm, digest, now)
            if inserted == "geo_restricted":  # 正文页命中地域封锁 → 整源标记并中止
                result["result"] = "geo_restricted"
                result["detail"] = "文章页返回地域封锁响应"
                _finalize(db_path, source, result, now)
                return result
            new_count += 1 if inserted else 0

        # 5) 成功一轮：更新条件 GET 凭据，恢复 geo_status=ok
        result["result"] = "ok"
        result["new_articles"] = new_count
        _set_geo(db_path, source["id"], "ok", None, None)
        _finalize(db_path, source, result, now,
                  etag=resp.headers.get("ETag"),
                  last_modified=resp.headers.get("Last-Modified"))
        return result


async def _fetch_and_store_article(db_path: str, client: httpx.AsyncClient,
                                   source: dict, entry: Any, norm_url: str,
                                   digest: str, now: str) -> str | bool:
    """抓取单篇文章页、抽取正文并入库；返回 True/False/'geo_restricted'。"""
    try:
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
    title = meta["title"] or getattr(entry, "title", None) or "(无标题)"
    body = meta["body"]
    # 摘要：正文前 200 字符；无正文时回退 feed summary（对外只输出短摘要，合规红线）
    feed_summary = _strip_tags(getattr(entry, "summary", "") or "")
    summary = (body or feed_summary)[:200] or None
    published = meta["published_at"] or _entry_published(entry)
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
    """统一收尾：更新源抓取状态 + 写 fetch_log。"""
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            """
            UPDATE sources SET last_fetched_at=?, last_status=?,
                   etag=COALESCE(?, etag), last_modified=COALESCE(?, last_modified)
            WHERE id=?
            """,
            (now, result["http_status"], etag, last_modified, source["id"]),
        )
        conn.execute(
            """
            INSERT INTO fetch_log (source_id, fetched_at, http_status, result,
                                   new_articles, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source["id"], now, result["http_status"], result["result"],
             result["new_articles"], result["detail"]),
        )
