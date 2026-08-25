"""per-host 礼貌限速（M1）：同一 host 两次请求的最小间隔（令牌桶简化实现）。

设计 5.2.1 礼貌爬取要求：同 host 最小间隔 2 秒，礼貌违规零容忍（M1-F5）。
实现为「上次请求时间 + per-host asyncio 锁」：同一 host 的请求串行通过，
不足最小间隔则 sleep 补齐；不同 host 互不阻塞。min_interval ≤ 0 时不限速
（测试用）。
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit


class HostRateLimiter:
    """按 host 限速：acquire(url) 返回时保证距该 host 上次请求 ≥ min_interval 秒。"""

    def __init__(self, min_interval: float = 2.0) -> None:
        self.min_interval = min_interval
        self._last: dict[str, float] = {}          # host -> 上次放行时间（monotonic）
        self._locks: dict[str, asyncio.Lock] = {}  # host -> 串行锁

    @staticmethod
    def _host(url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

    async def acquire(self, url: str) -> None:
        """等待直至可以向该 url 所在 host 发起下一次请求。"""
        if self.min_interval <= 0:
            return
        host = self._host(url)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host)
            now = time.monotonic()
            if last is not None:
                wait = self.min_interval - (now - last)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last[host] = time.monotonic()
