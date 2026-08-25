"""种子源加载：读取 sources/*.yaml 并 upsert 进 sources 表。

YAML schema 与 skills/news-source-onboarding 的 validate_source.py 保持一致。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .db import WRITE_LOCK, connect

_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_interval_minutes(duration: str, default: int = 10) -> int:
    """把 ISO 8601 duration（如 PT5M / PT1H）折算为分钟，解析失败用默认值。"""
    m = _DURATION_RE.match(duration or "")
    if not m:
        return default
    hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return max(1, hours * 60 + minutes + (1 if seconds else 0))


def load_seed_sources(db_path: str, sources_dir: Path) -> int:
    """把 sources/ 下的 YAML 源定义 upsert 进库（按 feed_url 判重），返回源数量。"""
    count = 0
    with WRITE_LOCK, connect(db_path) as conn:
        for path in sorted(sources_dir.glob("*.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                continue
            disc = doc.get("discovery") or {}
            if disc.get("strategy") != "rss" or not disc.get("feed_url"):
                continue  # MVP 只支持 rss 策略，其余策略留给 v1.0 四级降级
            interval = parse_interval_minutes(
                str((doc.get("update_profile") or {}).get("estimated_interval", ""))
            )
            geo = doc.get("geo") or {}
            conn.execute(
                """
                INSERT INTO sources (source_key, name, base_url, country, language,
                                     media_type, influence_tier, feed_url,
                                     interval_minutes, geo_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feed_url) DO UPDATE SET
                    name=excluded.name, base_url=excluded.base_url,
                    country=excluded.country, language=excluded.language,
                    media_type=excluded.media_type,
                    influence_tier=excluded.influence_tier,
                    interval_minutes=excluded.interval_minutes
                """,
                (
                    path.stem, doc.get("name", path.stem), doc.get("base_url", ""),
                    doc.get("country", ""), doc.get("language", ""),
                    doc.get("media_type"), doc.get("influence_tier"),
                    disc["feed_url"], interval, geo.get("status", "unknown"),
                ),
            )
            count += 1
    return count
