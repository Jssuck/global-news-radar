#!/usr/bin/env python3
"""校验 Global News Radar 源定义文件（YAML/JSON）是否符合 source-schema。

用法:
    python3 validate_source.py <source.yaml|source.json> [--json]

退出码: 0 = 全部通过; 1 = 存在错误; 2 = 文件无法解析
"""
import json
import re
import sys

ISO_COUNTRY = re.compile(r"^[a-z]{2}$")
BCP47 = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
ISO_DURATION = re.compile(r"^P(T\d+[HMS].*|\d+D.*|\d+W)$")
MEDIA_TYPES = {"agency", "newspaper", "tv", "radio", "online"}
TIERS = {"national", "major", "regional"}
STRATEGIES = {"rss", "sitemap", "html_list", "aggregator"}
GEO_STATUS = {"ok", "geo_restricted", "unknown"}


def load(path):
    text = open(path, encoding="utf-8").read()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)


def check(doc):
    errors, warnings = [], []

    def req(cond, msg):
        if not cond:
            errors.append(msg)

    req(isinstance(doc, dict), "根节点必须是映射对象")
    if not isinstance(doc, dict):
        return errors, warnings

    req(bool(doc.get("name")), "缺少必填字段 name")
    url = str(doc.get("base_url", ""))
    req(url.startswith("http"), "base_url 必须是 http(s) URL")
    req(bool(ISO_COUNTRY.match(str(doc.get("country", "")))), "country 必须是 ISO 3166-1 alpha-2 小写两字母")
    req(bool(BCP47.match(str(doc.get("language", "")))), "language 必须是 BCP-47 标签")
    req(doc.get("media_type") in MEDIA_TYPES, f"media_type 必须是 {sorted(MEDIA_TYPES)} 之一")
    req(doc.get("influence_tier") in TIERS, f"influence_tier 必须是 {sorted(TIERS)} 之一")

    disc = doc.get("discovery") or {}
    st = disc.get("strategy")
    req(st in STRATEGIES, f"discovery.strategy 必须是 {sorted(STRATEGIES)} 之一")
    if st == "rss":
        req(bool(disc.get("feed_url")), "strategy=rss 时 discovery.feed_url 必填")
    if st == "sitemap":
        req(bool(disc.get("sitemap_url")), "strategy=sitemap 时 discovery.sitemap_url 必填")
    if st == "html_list":
        req(bool(disc.get("list_url")) and bool(disc.get("link_selector")),
            "strategy=html_list 时 discovery.list_url 与 link_selector 必填")
        warnings.append("html_list 策略脆弱：请在 legal.notes 标注需黄金样本回归")
    if st == "aggregator" and not disc.get("degraded"):
        warnings.append("strategy=aggregator 建议标记 discovery.degraded: true")

    geo = doc.get("geo") or {}
    req(geo.get("status") in GEO_STATUS, f"geo.status 必须是 {sorted(GEO_STATUS)} 之一")
    if geo.get("status") == "geo_restricted":
        req(bool(ISO_COUNTRY.match(str(geo.get("required_region", "")))),
            "geo_restricted 时 geo.required_region 必填且为 ISO 国家码")
        req(bool(geo.get("evidence")), "geo_restricted 时 geo.evidence 必填（判定依据）")

    interval = str((doc.get("update_profile") or {}).get("estimated_interval", ""))
    req(bool(ISO_DURATION.match(interval)), "update_profile.estimated_interval 必须是 ISO 8601 duration")

    legal = doc.get("legal") or {}
    req(legal.get("robots_checked") is True, "legal.robots_checked 必须为 true（未检查 robots.txt 不得入库）")
    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    path = sys.argv[1]
    try:
        doc = load(path)
    except Exception as exc:  # noqa: BLE001
        print(f"PARSE_ERROR: {exc}")
        sys.exit(2)
    errors, warnings = check(doc)
    result = {"file": path, "valid": not errors, "errors": errors, "warnings": warnings}
    if "--json" in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for e in errors:
            print(f"ERROR: {e}")
        for w in warnings:
            print(f"WARN:  {w}")
        print("PASS" if not errors else "FAIL")
    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
