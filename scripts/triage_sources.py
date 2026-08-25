"""按 geo-block-triage skill 对抓取失败源做分诊，并把结论回写 sources/*.yaml。

判定规则（与 skills/geo-block-triage 一致，只处理证据充分的两类）：
- 最近 N 轮均为 robots_blocked → verdict=blocked_legal，active: false（合规红线：robots 禁止即不抓）
- 最近 N 轮均失败且最新为 anti_bot → verdict=anti_bot，active: false（需 JS 渲染，M2 里程碑恢复）
- 网络错误等证据不足 → verdict=inconclusive，保持 active，等待环境恢复后复测

用法:
    python3 scripts/triage_sources.py --db data/gnr.db [--sources-dir sources]
                                      [--min-rounds 2] [--apply]
默认 dry-run 只打印分诊表；--apply 才回写 YAML。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.db import connect

_OK = ("ok", "not_modified")
# result → (verdict, deactivate?)
_VERDICTS = {
    "robots_blocked": ("blocked_legal", True),
    "anti_bot": ("anti_bot", True),
}


def latest_rounds(conn, min_rounds: int) -> dict[str, list[str]]:
    """每个活跃源最近 min_rounds 轮的 result 序列（新→旧）。"""
    rows = conn.execute(
        """
        SELECT s.source_key, f.result,
               ROW_NUMBER() OVER (PARTITION BY s.source_key ORDER BY f.id DESC) rn
        FROM fetch_log f JOIN sources s ON s.id = f.source_id
        WHERE s.active = 1 AND f.result IS NOT NULL
        """
    ).fetchall()
    rounds: dict[str, list[str]] = {}
    for r in rows:
        if r["rn"] <= min_rounds:
            rounds.setdefault(r["source_key"], []).append(r["result"])
    return rounds


def classify(rounds: list[str], min_rounds: int) -> tuple[str, bool, str]:
    """单源分诊：返回 (verdict, deactivate, reason)。只处理持续失败。"""
    if not rounds or any(r in _OK for r in rounds):
        return "ok", False, ""
    if len(rounds) < min_rounds:
        return "inconclusive", False, f"轮次不足（{len(rounds)}<{min_rounds}）"
    latest = rounds[0]
    if latest in _VERDICTS:
        verdict, deactivate = _VERDICTS[latest]
        return verdict, deactivate, f"连续 {len(rounds)} 轮 {latest}"
    return "inconclusive", False, f"连续 {len(rounds)} 轮 {latest}（证据不足，保持活跃待复测）"


def apply_to_yaml(sources_dir: Path, key: str, verdict: str, reason: str,
                  today: str) -> bool:
    """把 active/triage 结论回写对应 YAML；文件不存在时返回 False。"""
    path = sources_dir / f"{key}.yaml"
    if not path.exists():
        return False
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    doc["active"] = False
    doc["triage"] = {"verdict": verdict, "evidence": reason, "date": today,
                     "revisit": "M2" if verdict == "anti_bot" else "manual"}
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="抓取库 SQLite 路径")
    ap.add_argument("--sources-dir", default=str(REPO_ROOT / "sources"))
    ap.add_argument("--min-rounds", type=int, default=2, help="判定持续失败所需轮数")
    ap.add_argument("--apply", action="store_true", help="回写 YAML（默认 dry-run）")
    ap.add_argument("--date", default=None, help="triage 日期（默认今天）")
    args = ap.parse_args()

    from datetime import datetime, timezone
    today = args.date or datetime.now(timezone.utc).date().isoformat()
    sources_dir = Path(args.sources_dir)

    with connect(args.db) as conn:
        rounds_map = latest_rounds(conn, args.min_rounds)

    deactivated, kept = [], []
    for key in sorted(rounds_map):
        verdict, deactivate, reason = classify(rounds_map[key], args.min_rounds)
        if verdict == "ok":
            continue
        (deactivated if deactivate else kept).append((key, verdict, reason))
        if deactivate and args.apply:
            apply_to_yaml(sources_dir, key, verdict, reason, today)

    print(f"分诊完成（min_rounds={args.min_rounds}, apply={args.apply}）")
    print(f"停用 {len(deactivated)} 个：")
    for key, verdict, reason in deactivated:
        print(f"  {verdict:14s} {key:40s} {reason}")
    print(f"保持活跃待复测 {len(kept)} 个：")
    for key, verdict, reason in kept:
        print(f"  {verdict:14s} {key:40s} {reason}")


if __name__ == "__main__":
    main()
