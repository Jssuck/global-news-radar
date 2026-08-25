"""robots.txt 合规检查（M1）：抓取前按 UA 判定是否允许抓取。

实现要点：
- 使用 stdlib urllib.robotparser 解析 robots.txt；
- robots.txt 经注入的 httpx client 抓取（测试可用 MockTransport 离线运行）；
- 解析结果按域名（scheme://host）缓存，默认 1 小时 TTL（设计 5.2.1 礼貌爬取要求）；
- 语义沿用 robotparser 惯例：401/403 视为全站禁止，其余非 200/网络错误视为允许
  （保守放行，宁可漏判不可误杀，违规风险由 per-host 限速兜底）。
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("gnr.robots")

DEFAULT_TTL = 3600  # robots 结果按域名缓存 1 小时


class RobotsCache:
    """按域名缓存的 robots 判定器；allowed() 为唯一入口。"""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL) -> None:
        self.ttl_seconds = ttl_seconds
        # host_key -> (缓存时间戳, RobotFileParser | None)；None 表示无 robots（全放行）
        self._cache: dict[str, tuple[float, RobotFileParser | None]] = {}

    @staticmethod
    def host_key(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}"

    async def _load(self, client: httpx.AsyncClient, host_key: str):
        """抓取并解析 robots.txt；失败时按惯例构造放行/禁止规则。"""
        rp = RobotFileParser(url=f"{host_key}/robots.txt")
        try:
            resp = await client.get(f"{host_key}/robots.txt")
        except httpx.HTTPError as exc:
            log.info("robots.txt 抓取失败 %s: %s（按放行处理）",
                     host_key, exc.__class__.__name__)
            return None
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
            return rp
        if resp.status_code in (401, 403):
            # 惯例：401/403 视为全站禁止抓取
            rp.parse(["User-agent: *", "Disallow: /"])
            return rp
        # 404 及其他状态：无 robots 约束，全放行
        return None

    async def _parser_for(self, client: httpx.AsyncClient, host_key: str):
        entry = self._cache.get(host_key)
        if entry and time.monotonic() - entry[0] < self.ttl_seconds:
            return entry[1]
        parser = await self._load(client, host_key)
        self._cache[host_key] = (time.monotonic(), parser)
        return parser

    async def allowed(self, client: httpx.AsyncClient, url: str,
                      user_agent: str) -> bool:
        """按 UA 判定 url 是否允许抓取；无 robots 或抓取失败时放行。"""
        host_key = self.host_key(url)
        parser = await self._parser_for(client, host_key)
        if parser is None:
            return True
        try:
            ok = parser.can_fetch(user_agent, url)
        except Exception:  # robotparser 对异常 URL 可能抛错，按放行处理
            log.debug("robotparser 判定异常，按放行处理: %s", url, exc_info=True)
            return True
        if not ok:
            log.info("robots.txt 禁止抓取: %s (UA=%s)", url, user_agent)
        return ok

    async def sitemaps(self, client: httpx.AsyncClient, base_url: str) -> list[str]:
        """返回该站 robots.txt 中声明的 Sitemap 地址列表（无则空列表）。"""
        host_key = self.host_key(base_url)
        parser = await self._parser_for(client, host_key)
        if parser is None:
            return []
        return list(parser.site_maps() or [])


# 进程级默认缓存（pipeline 未注入时使用）
_default_cache: RobotsCache | None = None


def default_robots_cache() -> RobotsCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = RobotsCache()
    return _default_cache
