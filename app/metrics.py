"""M1 验收指标汇总：stats 端点与 scripts/collect_metrics.py 共用的统计口径。

指标来源：fetch_log 的逐轮计数（extraction_ok/total、dedup_hits、result）。
口径：
- extraction_success_rate = 累计抽取成功篇数 / 累计抓取尝试篇数（M1-F2）；
- rss_discovery_ok/total = 活跃源最近一轮抓取结果 ok/not_modified 的源数 / 总活跃源数；
- dedup_hits = 累计哈希去重命中数（M1-F4 重复摄入对照用）。
"""
from __future__ import annotations

from .db import connect

# 抓取结果中视为「发现成功」的取值
_OK_RESULTS = ("ok", "not_modified")


def extraction_stats(conn) -> tuple[int, int]:
    """累计（抽取成功篇数, 抓取尝试篇数）。"""
    row = conn.execute(
        "SELECT COALESCE(SUM(extraction_ok),0) AS ok,"
        " COALESCE(SUM(extraction_total),0) AS total FROM fetch_log"
    ).fetchone()
    return row["ok"], row["total"]


def discovery_stats(conn) -> tuple[int, int]:
    """按活跃源最近一轮 fetch_log 统计（发现成功源数, 活跃源总数）。"""
    rows = conn.execute(
        """
        SELECT f.result FROM fetch_log f
        JOIN (SELECT source_id, MAX(id) AS max_id FROM fetch_log GROUP BY source_id) t
          ON t.max_id = f.id
        JOIN sources s ON s.id = f.source_id
        WHERE s.active = 1
        """
    ).fetchall()
    ok = sum(1 for r in rows if r["result"] in _OK_RESULTS)
    return ok, len(rows)


def dedup_hits(conn) -> int:
    """累计哈希去重命中数。"""
    return conn.execute(
        "SELECT COALESCE(SUM(dedup_hits),0) FROM fetch_log"
    ).fetchone()[0]


def compute_metrics(db_path: str) -> dict:
    """M1 验收 metrics（键名与 gate_check.py 的 metrics 输入约定一致）。"""
    with connect(db_path) as conn:
        sources_total = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        sources_active = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE active = 1").fetchone()[0]
        articles_total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        covered = conn.execute(
            "SELECT COUNT(DISTINCT source_id) FROM articles").fetchone()[0]
        ext_ok, ext_total = extraction_stats(conn)
        disc_ok, disc_total = discovery_stats(conn)
        dedup = dedup_hits(conn)
    return {
        "sources_onboarded": sources_total,
        "sources_active": sources_active,
        "articles_total": articles_total,
        "rss_discovery_rate": (disc_ok / disc_total) if disc_total else None,
        "rss_discovery_ok": disc_ok,
        "rss_discovery_total": disc_total,
        "extraction_success": (ext_ok / ext_total) if ext_total else None,
        "extraction_ok": ext_ok,
        "extraction_total": ext_total,
        "coverage": (covered / sources_active) if sources_active else None,
        "sources_with_articles": covered,
        "dedup_hits": dedup,
    }
