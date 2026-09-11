"""地域受限检测与提示文案（M2 版：强/中信号分级 + 他国出口对照确认）。

判定逻辑移植自项目 skill：skills/geo-block-triage/scripts/geo_verdict.py
与 references/signal-rules.md 保持一致：
- 强信号：HTTP 451（RFC 7725）/ 403+地域语料 → 直接判 geo_restricted
- 中信号：地域警告页重定向 / 正文异常截断 → geo_suspected，
  有他国代理出口时做一次对照验证（取到完整内容才确认 geo_restricted，
  且 required_region 由对照出口所在国反推），无出口则保持 suspected
- 排除项：429 / 5xx / JS 挑战页 → anti_bot，不进入地域判定
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
# 地域警告页特征：重定向目标 URL 含 geo/region 提示（设计 3.3.1 中信号）
GEO_REDIRECT_HINTS = ("geo-block", "geo_block", "geoblock", "region-locked",
                      "region_locked", "regionlock", "/geo/", "blocked-country",
                      "not-available-in-your")

# 正文截断判定：相对该源历史正文长度中位数的骤降比例（signal-rules：骤降 ≥90%）
TRUNCATION_DROP = 0.9
TRUNCATION_MIN_SAMPLES = 5  # 历史样本不足不做截断判定


@dataclass
class GeoVerdict:
    verdict: str                    # geo_restricted | geo_suspected | anti_bot | ok
    reason: str
    signals: list[str] = field(default_factory=list)
    confirmed_region: str | None = None  # 对照确认时反推出的所需出口国


def evaluate_response(status: int | None, body: str = "",
                      *, final_url: str | None = None,
                      request_url: str | None = None) -> GeoVerdict:
    """对一次 HTTP 响应执行地域受限信号判定（强/中/排除三级）。

    final_url 为重定向后的最终 URL（无重定向时与 request_url 相同）。
    """
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

    # 中信号 1：重定向至地域警告页
    if final_url and request_url and final_url != request_url:
        tail = final_url.lower()
        if any(h in tail for h in GEO_REDIRECT_HINTS):
            return GeoVerdict(
                "geo_suspected",
                f"重定向至疑似地域警告页 {final_url}，中信号待对照验证",
                ["geo_redirect"],
            )

    return GeoVerdict("ok", f"无地域受限信号（status={status}）")


def evaluate_truncation(body_len: int, history_lens: list[int]) -> GeoVerdict | None:
    """中信号 2：正文异常截断——本次长度 < 历史中位数 ×(1-TRUNCATION_DROP)。

    历史样本不足 TRUNCATION_MIN_SAMPLES 时不下结论（返回 None）。
    """
    if len(history_lens) < TRUNCATION_MIN_SAMPLES or body_len <= 0:
        return None
    ordered = sorted(history_lens)
    median = ordered[len(ordered) // 2]
    if median > 0 and body_len < median * (1 - TRUNCATION_DROP):
        return GeoVerdict(
            "geo_suspected",
            f"正文长度 {body_len} 较历史中位数 {median} 骤降 ≥90%，疑似软封锁",
            ["body_truncation"],
        )
    return None


def confirmed_by_contrast(direct_ok: bool, via_proxy_ok: bool,
                          proxy_country: str | None) -> tuple[bool, str | None, str]:
    """他国出口对照验证结论（设计 3.3.1：中信号经对照复现差异才确认）。

    返回 (是否确认为地域受限, required_region, 证据文案)。
    direct_ok=False 且 via_proxy_ok=True → 确认，required_region=代理出口国。
    """
    if not direct_ok and via_proxy_ok and proxy_country:
        return True, proxy_country, (
            f"直连失败而 {proxy_country.upper()} 出口取得完整内容，对照确认地域受限")
    if direct_ok:
        return False, None, "直连成功，排除地域受限"
    return False, None, f"{(proxy_country or '他国').upper()} 出口对照同样失败，证据不足"


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


def geo_hint(required_region: str | None, evidence: str | None = None,
             *, suspected: bool = False) -> str:
    """生成用户可见的地域代理提示文案（设计 3.3.2）。

    suspected=True 为中信号未确认状态：提示用户需要他国出口做对照验证。
    """
    name = region_name(required_region)
    if suspected:
        hint = f"该媒体疑似仅允许 {name} 地区 IP 访问（中信号，未确认）。"
    else:
        hint = f"该媒体仅允许 {name} 地区 IP 访问，需要添加 {name} 地域的代理。"
    if evidence:
        hint += f"（检测依据：{evidence}）"
    hint += "请在 config/proxies.yaml 或 /api/v1/proxy-profiles 配置对应地域的代理（参考 config/proxies.example.yaml），系统不会静默切换代理，是否经代理访问由您自行决定并承担合规责任。"
    return hint
