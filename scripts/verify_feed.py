"""批量验证 sources/*.yaml 中 RSS feed 的可用性（M1 源入库验收工具）。

用法:
    python3 scripts/verify_feed.py [--dir sources] [--workers 16] [--timeout 15] [--json out.json] [--quiet]

判定: HTTP 200 且 feedparser 可解析且 entries>0 且最新条目 ≤7 天 → ok
输出: 汇总统计 + 逐源状态; 退出码 0=全部ok 1=存在失败 2=用法错误
"""
import argparse
import concurrent.futures as cf
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
import yaml

UA = "GlobalNewsRadar-M1-Verifier/0.1 (+https://github.com/Jssuck/global-news-radar)"


def check_one(path: Path, timeout: float) -> dict:
    rec = {"file": path.name, "status": "error", "http": None, "entries": 0,
           "fresh": False, "latency_s": None, "error": None}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        url = (doc.get("discovery") or {}).get("feed_url")
        rec["name"] = doc.get("name")
        rec["country"] = doc.get("country")
        rec["language"] = doc.get("language")
        if not url:
            rec["error"] = "no feed_url"
            return rec
        t0 = time.time()
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": UA})
        rec["latency_s"] = round(time.time() - t0, 2)
        rec["http"] = r.status_code
        if r.status_code != 200:
            rec["status"] = "http_fail"
            return rec
        feed = feedparser.parse(r.content)
        rec["entries"] = len(feed.entries)
        if rec["entries"] == 0:
            rec["status"] = "parse_fail"
            return rec
        newest = None
        for e in feed.entries[:5]:
            tt = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            if tt:
                dt = datetime(*tt[:6], tzinfo=timezone.utc)
                newest = dt if newest is None or dt > newest else newest
        if newest is None:
            rec["fresh"] = True  # 无时间戳不判死
        else:
            rec["fresh"] = (datetime.now(timezone.utc) - newest).days <= 7
        rec["status"] = "ok" if rec["fresh"] else "stale"
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="sources")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    files = sorted(Path(args.dir).glob("*.yaml"))
    if not files:
        print(f"no yaml in {args.dir}", file=sys.stderr)
        sys.exit(2)
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(check_one, f, args.timeout): f for f in files}
        for fut in cf.as_completed(futs):
            rec = fut.result()
            results.append(rec)
            if not args.quiet:
                mark = "OK " if rec["status"] == "ok" else "BAD"
                print(f"[{mark}] {rec['file']:<32} {rec.get('status')} http={rec.get('http')} "
                      f"entries={rec.get('entries')} {rec.get('error') or ''}")

    ok = sum(1 for r in results if r["status"] == "ok")
    summary = {"total": len(results), "ok": ok,
               "success_rate": round(ok / len(results), 4),
               "by_status": {}}
    for r in results:
        summary["by_status"][r["status"]] = summary["by_status"].get(r["status"], 0) + 1
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"summary": summary, "results": sorted(results, key=lambda x: x["file"])},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== {ok}/{len(results)} ok ({summary['success_rate']*100:.1f}%) == {summary['by_status']}")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
