#!/usr/bin/env python3
"""按 signal-rules 对疑似地域封锁信号计分并输出 verdict。

用法:
    python3 geo_verdict.py [--status 451] [--body-hit "phrase"]... [--redirect-geo]
        [--len-ratio 0.1] [--exit-mismatch] [--retry-stable] [--json]

参数:
    --status N          HTTP 状态码
    --body-hit TEXT     响应正文中命中的地域封锁语料（可多次）
    --redirect-geo      重定向到地域警告页
    --len-ratio R       当前正文长度 / 历史 p10（<0.1 视为截断）
    --exit-mismatch     多出口对照：非目标国失败而目标国 200
    --retry-stable      同 IP 重试 3 次结果不变
    --json              JSON 输出
退出码: 0 恒定（verdict 见输出）
"""
import argparse
import json

ANTI_BOT_STATUS = {429, 500, 502, 503}
GEO_PHRASES = [
    "not available in your region", "unavailable in your location",
    "country or region", "do not provide services", "not available in your country",
]


def verdict(args):
    anti_bot = args.status in ANTI_BOT_STATUS or any(
        "captcha" in h.lower() or "challenge" in h.lower() for h in args.body_hit
    )
    if anti_bot:
        return "anti_bot", "命中反爬特征（429/5xx/挑战页），排除地域判定"

    strong = []
    if args.status == 451:
        strong.append("http_451")
    body_geo = [h for h in args.body_hit if any(p in h.lower() for p in GEO_PHRASES)]
    if args.status == 403 and body_geo:
        strong.append("http_403_geo_phrase")

    medium = []
    if args.redirect_geo:
        medium.append("redirect_geo_page")
    if args.len_ratio is not None and args.len_ratio < 0.1:
        medium.append("body_truncated")
    if args.exit_mismatch:
        medium.append("multi_exit_mismatch")

    auxiliary = bool(args.retry_stable)

    if strong and auxiliary:
        return "geo_restricted", f"强信号 {strong} + 复现确认"
    if len(medium) >= 2:
        return "geo_restricted", f"中信号×{len(medium)} {medium}，无反爬特征"
    return "inconclusive", f"证据不足（强={strong} 中={medium} 辅助={auxiliary}），24h 后复测"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", type=int, default=None)
    ap.add_argument("--body-hit", action="append", default=[])
    ap.add_argument("--redirect-geo", action="store_true")
    ap.add_argument("--len-ratio", type=float, default=None)
    ap.add_argument("--exit-mismatch", action="store_true")
    ap.add_argument("--retry-stable", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    v, reason = verdict(args)
    out = {"verdict": v, "reason": reason}
    print(json.dumps(out, ensure_ascii=False) if args.json else f"{v}\n{reason}")


if __name__ == "__main__":
    main()
