"""渲染兜底 worker（设计 4.1 渲染路径，M2 可选启用）。

仅对 anti_bot 判定的源启用：Playwright 无头浏览器取渲染后 HTML 再进
规则抽取链。约束：
- 可选依赖：playwright 未安装/未启用时渲染路径整体不存在；
- 预算限制：每日渲染次数上限（render_daily_budget），超限不再尝试——
  渲染只应是少数源的路径（设计预算口径 ≤15%）；
- 合规：只模拟常规浏览器渲染公开页面，不做指纹伪装/验证码破解/
  登录态伪造（见 docs/harness/04-code-quality.md 红线）。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from .db import connect

log = logging.getLogger("gnr.render")

try:  # 可选依赖，未安装则整条渲染路径不可用
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover
    async_playwright = None


def render_available() -> bool:
    return async_playwright is not None


def renders_today(db_path: str) -> int:
    """今日已渲染次数（fetch_log.result='rendered' 计数）。"""
    today = datetime.now(UTC).date().isoformat()
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM fetch_log WHERE result='rendered'"
            " AND fetched_at >= ?",
            (today,),
        ).fetchone()[0]


def budget_ok(db_path: str, settings) -> bool:
    return renders_today(db_path) < settings.render_daily_budget


async def render_fetch(url: str, settings, *, proxy: str | None = None,
                       wait_ms: int = 3000) -> str | None:
    """无头浏览器渲染取 HTML；不可用/失败返回 None。"""
    if async_playwright is None:
        return None
    try:
        async with async_playwright() as pw:
            launch_kwargs: dict = {"headless": True}
            if proxy:
                launch_kwargs["proxy"] = {"server": proxy}
            browser = await pw.chromium.launch(**launch_kwargs)
            try:
                page = await browser.new_page(
                    user_agent=settings.user_agent)
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=int(settings.request_timeout * 1000))
                await page.wait_for_timeout(wait_ms)
                return await page.content()
            finally:
                await browser.close()
    except Exception:  # 渲染失败按不可用处理，回退原路径
        log.info("渲染失败 %s", url, exc_info=True)
        return None
