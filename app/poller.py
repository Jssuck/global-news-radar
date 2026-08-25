"""后台轮询任务：按源级间隔（interval_minutes）到期抓取，单进程 asyncio 实现。

对应设计 3.2.2「条件 GET 轮询」的 MVP 版：无 WebSub、无自适应退避，
间隔固定取源配置（默认 10 分钟），可用 GNR_POLL_INTERVAL 设全局下限。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from .db import connect
from .pipeline import fetch_source

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
    """轮询主循环：每 15 秒检查一次到期源，直到 stop_event 置位。"""
    log.info("poller started (default interval %ss)", settings.poll_interval)
    while not stop_event.is_set():
        try:
            for src in _due_sources(db_path, settings.poll_interval):
                if stop_event.is_set():
                    break
                result = await fetch_source(db_path, src, settings)
                log.info("fetch %s -> %s (+%s)", src["source_key"],
                         result["result"], result["new_articles"])
        except Exception:  # 单源异常不拖垮整个轮询循环
            log.exception("poll iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except TimeoutError:
            pass
    log.info("poller stopped")
