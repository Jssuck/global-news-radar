"""黄金样本回归工具（M1-F2 正文抽取成功率 ≥90% 的回归底座）。

record 子命令（联网）：对 sources/ 中每源抓 ≤3 篇文章，把 原始 HTML + 抽取结果 +
元数据 存档到 tests/golden/<source_id>/（manifest.json 记录 url/抽取正文/时间）。
check  子命令（离线）：用缓存 HTML 重跑当前抽取链，逐字段 diff，输出每源通过率
与总体成功率（--json），失败明细。

用法:
    python3 scripts/golden_regression.py record [--sources-dir sources] [--per-source 3]
    python3 scripts/golden_regression.py check [--golden-dir tests/golden] [--json]
退出码: check 模式下 0=成功率达标 1=低于阈值 2=用法/输入错误
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.cleaner import extract_article
from app.sitemap import filter_recent, parse_sitemap

UA = "GlobalNewsRadar-M1-Golden/0.1 (+https://github.com/Jssuck/global-news-radar)"
# 逐字段 diff 的字段清单（抽取结果）
DIFF_FIELDS = ("title", "body", "published_at")


# ---------------- check（离线） ----------------

def check_golden(golden_dir: Path) -> dict:
    """离线重跑抽取链并逐字段 diff，返回结构化报告。"""
    report: dict = {"total": 0, "passed": 0, "success_rate": None,
                    "sources": [], "failures": []}
    if not golden_dir.is_dir():
        return report
    for src_dir in sorted(p for p in golden_dir.iterdir() if p.is_dir()):
        manifest_path = src_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        src_pass = src_total = 0
        for entry in manifest.get("entries", []):
            html_path = src_dir / entry["file"]
            if not html_path.is_file():
                report["failures"].append(
                    {"source": src_dir.name, "url": entry.get("url"),
                     "field": "file", "expected": entry["file"], "actual": "missing"})
                src_total += 1
                continue
            html = html_path.read_text(encoding="utf-8")
            meta = extract_article(html, entry.get("url") or "")
            src_total += 1
            diffs = []
            for field in DIFF_FIELDS:
                expected = entry.get("extracted", {}).get(field)
                actual = meta.get(field)
                if expected != actual:
                    diffs.append({"source": src_dir.name, "url": entry.get("url"),
                                  "field": field,
                                  "expected": _clip(expected),
                                  "actual": _clip(actual)})
            if diffs:
                report["failures"].extend(diffs)
            else:
                src_pass += 1
        report["total"] += src_total
        report["passed"] += src_pass
        report["sources"].append({
            "source": src_dir.name, "passed": src_pass, "total": src_total,
            "pass_rate": (src_pass / src_total) if src_total else None})
    if report["total"]:
        report["success_rate"] = report["passed"] / report["total"]
    return report


def _clip(value, limit: int = 80):
    """失败明细中的字段值截断，避免输出整段正文。"""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    return value


# ---------------- record（联网） ----------------

def _candidate_urls(doc: dict, per_source: int, timeout: float) -> list[str]:
    """按发现策略取候选文章 URL：rss 读 feed，sitemap 解析 sitemap。"""
    import feedparser
    import httpx

    disc = doc.get("discovery") or {}
    strategy = disc.get("strategy", "rss")
    headers = {"User-Agent": UA}
    if strategy == "rss" and disc.get("feed_url"):
        resp = httpx.get(disc["feed_url"], timeout=timeout,
                         follow_redirects=True, headers=headers)
        feed = feedparser.parse(resp.text)
        return [e.link for e in feed.entries[:per_source] if getattr(e, "link", None)]
    # sitemap 策略：sitemap_url 或 robots.txt 声明，取近 48h（lastmod 仅提示）
    base = (doc.get("base_url") or "").rstrip("/")
    seeds = [disc["sitemap_url"]] if disc.get("sitemap_url") else [f"{base}/sitemap.xml"]
    urls: list[str] = []
    for seed in seeds:
        if len(urls) >= per_source:
            break
        try:
            resp = httpx.get(seed, timeout=timeout, follow_redirects=True,
                             headers=headers)
            parsed = parse_sitemap(resp.text)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            print(f"  [warn] sitemap 解析失败 {seed}: {exc}")
            continue
        queue = [loc for loc, _ in parsed["sitemaps"]]
        for loc, lastmod in filter_recent(parsed["urls"]):
            urls.append(loc)
            if len(urls) >= per_source:
                break
        for child in queue:  # sitemap index 嵌套只展开一层（record 够用）
            if len(urls) >= per_source:
                break
            try:
                resp = httpx.get(child, timeout=timeout, follow_redirects=True,
                                 headers=headers)
                sub = parse_sitemap(resp.text)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                print(f"  [warn] 子 sitemap 解析失败 {child}: {exc}")
                continue
            for loc, _ in filter_recent(sub["urls"]):
                urls.append(loc)
                if len(urls) >= per_source:
                    break
    return urls[:per_source]


def record(sources_dir: Path, golden_dir: Path, per_source: int,
           timeout: float) -> dict:
    """对每源抓 ≤per_source 篇文章，存档 HTML + 抽取结果 + manifest。"""
    import httpx
    import yaml

    summary = {"sources": 0, "articles": 0, "skipped": []}
    for path in sorted(sources_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        source_id = path.stem
        try:
            urls = _candidate_urls(doc, per_source, timeout)
        except (httpx.HTTPError, ValueError, OSError, KeyError) as exc:
            print(f"[skip] {source_id}: 候选发现失败: {exc}")
            summary["skipped"].append(source_id)
            continue
        if not urls:
            summary["skipped"].append(source_id)
            continue
        out_dir = golden_dir / source_id
        out_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for i, url in enumerate(urls):
            try:
                resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": UA})
                if resp.status_code != 200:
                    continue
            except (httpx.HTTPError, OSError) as exc:
                print(f"  [warn] {source_id} 文章抓取失败 {url}: {exc}")
                continue
            html = resp.text
            meta = extract_article(html, url)
            fname = f"article-{i}.html"
            (out_dir / fname).write_text(html, encoding="utf-8")
            entries.append({
                "file": fname, "url": url,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "extracted": {f: meta.get(f) for f in DIFF_FIELDS},
                "extractor": meta.get("extractor"),
                "body_len": len(meta.get("body") or "")})
            time.sleep(1)  # record 为人工运维操作，保守限速
        if entries:
            (out_dir / "manifest.json").write_text(
                json.dumps({"source_id": source_id,
                            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                         time.gmtime()),
                            "entries": entries},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
            summary["sources"] += 1
            summary["articles"] += len(entries)
            print(f"[ok] {source_id}: {len(entries)} 篇")
        else:
            summary["skipped"].append(source_id)
    return summary


# ---------------- CLI ----------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="联网抓取并存档黄金样本")
    rec.add_argument("--sources-dir", default=str(REPO_ROOT / "sources"))
    rec.add_argument("--golden-dir", default=str(REPO_ROOT / "tests/golden"))
    rec.add_argument("--per-source", type=int, default=3)
    rec.add_argument("--timeout", type=float, default=15)
    chk = sub.add_parser("check", help="离线重跑抽取链并 diff")
    chk.add_argument("--golden-dir", default=str(REPO_ROOT / "tests/golden"))
    chk.add_argument("--threshold", type=float, default=0.9,
                     help="总体成功率阈值（默认 0.9，对应 M1-F2）")
    chk.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.command == "record":
        summary = record(Path(args.sources_dir), Path(args.golden_dir),
                         args.per_source, args.timeout)
        print(f"record 完成: {summary['sources']} 源 / {summary['articles']} 篇，"
              f"跳过 {len(summary['skipped'])} 源")
        return

    report = check_golden(Path(args.golden_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for src in report["sources"]:
            rate = f"{src['pass_rate']:.0%}" if src["pass_rate"] is not None else "-"
            print(f"[{src['source']}] 通过率 {src['passed']}/{src['total']} ({rate})")
        rate = report["success_rate"]
        if rate is not None:
            print(f"总体成功率: {report['passed']}/{report['total']} ({rate:.1%})")
        else:
            print("总体成功率: 无样本")
        for fail in report["failures"][:20]:
            print(f"  FAIL {fail['source']} {fail['field']}: "
                  f"expected={fail['expected']!r} actual={fail['actual']!r}")
    rate = report["success_rate"]
    if rate is None or rate < args.threshold:
        sys.exit(1)


if __name__ == "__main__":
    main()
