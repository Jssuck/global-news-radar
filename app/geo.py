"""地域受限检测与提示文案。

判定逻辑移植自项目 skill：skills/geo-block-triage/scripts/geo_verdict.py
（M0 治理仓库自带脚本），与 signal-rules 保持一致：
- HTTP 451（RFC 7725）→ 强信号，直接判 geo_restricted
- HTTP 403 + 正文命中地域封锁语料 → 强信号
- 429 / 5xx / 挑战页 → 反爬特征，显式排除地域判定
MVP 简化：不实现「警告页重定向 / 正文截断 / 多出口对照」中信号的确认流程，
required_region 直接取源所属国家（假设本国 IP 必被允许），待 v1.0 用对照实验反推。
"""
from __future__ import annotations

from dataclasses import dataclass, field

ANTI_BOT_STATUS = {429, 500, 502, 503}
GEO_PHRASES = [
    "not available in your region",
    "unavailable in your location",
    "country or region",
    "do not provide services",
    "not available in your country",
]
CHALLENGE_HINTS = ("captcha", "challenge")


@dataclass
class GeoVerdict:
    verdict: str                    # geo_restricted | anti_bot | ok
    reason: str
    signals: list[str] = field(default_factory=list)


def evaluate_response(status: int | None, body: str = "") -> GeoVerdict:
    """对一次 HTTP 响应执行地域受限信号判定。"""
    lowered = body[:8000].lower()

    # 排除项：限流 / 服务端故障 / JS 挑战属反爬而非地域限制（设计 3.3.1）
    if status in ANTI_BOT_STATUS or any(h in lowered for h in CHALLENGE_HINTS):
        return GeoVerdict("anti_bot", f"命中反爬特征（status={status}），排除地域判定")

    if status == 451:
        return GeoVerdict(
            "geo_restricted",
            "HTTP 451（RFC 7725：因法律要求不可用），强信号直接判定",
            ["http_451"],
        )

    hits = [p for p in GEO_PHRASES if p in lowered]
    if status == 403 and hits:
        return GeoVerdict(
            "geo_restricted",
            f"HTTP 403 + 地域封锁语料 {hits}，强信号直接判定",
            ["http_403_geo_phrase"],
        )

    return GeoVerdict("ok", f"无地域受限信号（status={status}）")


# 常见国家/地区中文名，用于提示文案；未收录时回退为国家码大写
REGION_NAMES = {
    "us": "美国", "gb": "英国", "de": "德国", "jp": "日本", "cn": "中国",
    "fr": "法国", "kr": "韩国", "es": "西班牙", "it": "意大利", "qa": "卡塔尔",
    "in": "印度", "ru": "俄罗斯", "br": "巴西", "au": "澳大利亚", "ca": "加拿大",
}


def region_name(code: str | None) -> str:
    if not code:
        return "特定"
    return REGION_NAMES.get(code.lower(), code.upper())


def geo_hint(required_region: str | None, evidence: str | None = None) -> str:
    """生成用户可见的地域代理提示文案（设计 3.3.2 的 MVP 文字版）。"""
    name = region_name(required_region)
    hint = f"该媒体仅允许 {name} 地区 IP 访问，需要添加 {name} 地域的代理。"
    if evidence:
        hint += f"（检测依据：{evidence}）"
    hint += "请在 config/proxies.yaml 中配置对应地域的代理（参考 config/proxies.example.yaml），系统不会静默切换代理，是否经代理访问由您自行决定并承担合规责任。"
    return hint
