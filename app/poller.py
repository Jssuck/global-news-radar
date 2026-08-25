"""后台轮询任务：按源级间隔（interval_minutes）到期抓取，单进程 asyncio 实现。

对应设计 3.2.2「条件 GET 轮询」的 M1 版：
- 自适应轮询间隔：连续 2 轮无新文章 ×1.5（上限 60min），有新文章 ÷1.5
  （下限 5min），状态落库 sources.interval_minutes / empty_streak（见 pipeline）；
- 并发抓取：asyncio 信号量限制并发（默认 16，GNR_MAX_CONCURRENCY）；
- per-host 礼貌限速：同 host 最小间隔 2s（GNR_PER_HOST_MIN_INTERVAL，M1-F5）；
- robots.txt 合规：进程级 RobotsCache 按域名缓存 1 小时。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from .db import connect
from .pipeline import fetch_source
from .ratelimit import HostRateLimiter
from .robots import RobotsCache

log = logging.getLogger("gnr.poller")


def _due_sources(db_path: str, default_interval: int) -> list[dict]:
    """取到期源：从未抓取，或距上次抓取超过源级间隔。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM sources WHERE active = 1"
        ).fetchall()
    now = datetime.now(UTC)
    due = []
    for row in rows:
        src = dict(row)
        last = src.get("last_fetched_at")
        interval = timedelta(minutes=src["interval_minutes"])
        if interval.total_seconds() < default_interval:
            interval = timedelta(seconds=default_interval)
        if not last:
            due.append(src)
            continue
        try:
            if now - datetime.fromisoformat(last) >= interval:
                due.append(src)
        except ValueError:
            due.append(src)
    return due


async def poll_loop(db_path: str, settings, stop_event: asyncio.Event) -> None:
    """轮询主循环：每 15 秒检查一次到期源，信号量并发抓取，直到 stop_event 置位。"""
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    limiter = HostRateLimiter(settings.per_host_min_interval)
    robots = RobotsCache()
    log.info("poller started (default interval %ss, concurrency %s, host gap %ss)",
             settings.poll_interval, settings.max_concurrency,
             settings.per_host_min_interval)
    while not stop_event.is_set():
        try:
            due = _due_sources(db_path, settings.poll_interval)
            if due:
                await asyncio.gather(
                    *(_fetch_one(db_path, src, settings, semaphore, robots,
                                 limiter, stop_event) for src in due),
                    return_exceptions=True,  # 单源异常不拖垮整个轮询循环
                )
        except Exception:
            log.exception("poll iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except TimeoutError:
            pass
    log.info("poller stopped")


async def _fetch_one(db_path: str, src: dict, settings,
                     semaphore: asyncio.Semaphore, robots: RobotsCache,
                     limiter: HostRateLimiter, stop_event: asyncio.Event) -> None:
    """信号量保护下抓取单源；异常仅记日志。"""
    if stop_event.is_set():
        return
    async with semaphore:
        if stop_event.is_set():
            return
        try:
            result = await fetch_source(db_path, src, settings,
                                        robots=robots, limiter=limiter)
            log.info("fetch %s -> %s (+%s, dedup %s)",
                     src["source_key"], result["result"],
                     result["new_articles"], result["dedup_hits"])
        except Exception:
            log.exception("fetch %s failed", src["source_key"])
