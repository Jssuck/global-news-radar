"""跑一次全量抓取循环并输出 M1 验收 metrics JSON（供 gate_check.py --metrics 使用）。

用法:
    python3 scripts/collect_metrics.py [--db data/gnr.db] [--sources-dir sources]
                                       [--json-out m1_metrics.json]
输出键: sources_onboarded、rss_discovery_rate、extraction_success、coverage 等
（口径见 app/metrics.py；rate 类指标无样本时为 null）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings
from app.db import connect, init_db
from app.metrics import compute_metrics
from app.pipeline import fetch_source
from app.ratelimit import HostRateLimiter
from app.robots import RobotsCache
from app.sources_loader import load_seed_sources


async def run_crawl_cycle(db_path: str, settings) -> list[dict]:
    """对所有活跃源并发跑一轮抓取（信号量并发 + per-host 限速 + robots 缓存）。"""
    with connect(db_path) as conn:
        sources = [dict(r) for r in conn.execute(
            "SELECT * FROM sources WHERE active = 1").fetchall()]
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    limiter = HostRateLimiter(settings.per_host_min_interval)
    robots = RobotsCache()

    async def _one(src: dict) -> dict:
        async with semaphore:
            try:
                return await fetch_source(db_path, src, settings,
                                          robots=robots, limiter=limiter)
            except Exception as exc:  # 单源异常不中断全量循环
                logging.getLogger("gnr.collect").exception(
                    "fetch %s failed", src.get("source_key"))
                return {"source_id": src["id"], "result": "error",
                        "new_articles": 0, "detail": str(exc)}

    return list(await asyncio.gather(*(_one(s) for s in sources)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="SQLite 路径（默认取 GNR_DB_PATH）")
    ap.add_argument("--sources-dir", default=str(REPO_ROOT / "sources"))
    ap.add_argument("--json-out", default=None, help="同时写入 JSON 文件")
    args = ap.parse_args()

    settings = get_settings()
    db_path = args.db or settings.db_path
    init_db(db_path)
    n = load_seed_sources(db_path, Path(args.sources_dir))
    print(f"已加载 {n} 个源，开始全量抓取循环...", file=sys.stderr)

    results = asyncio.run(run_crawl_cycle(db_path, settings))
    ok = sum(1 for r in results if r["result"] in ("ok", "not_modified"))
    print(f"抓取循环完成: {ok}/{len(results)} 源成功", file=sys.stderr)

    metrics = compute_metrics(db_path)
    out = json.dumps(metrics, ensure_ascii=False, indent=2)
    print(out)
    if args.json_out:
        Path(args.json_out).write_text(out + "\n", encoding="utf-8")
        print(f"已写入 {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
