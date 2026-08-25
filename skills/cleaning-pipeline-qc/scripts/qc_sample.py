"""按语种分层随机抽样生成 QC 抽检批次。

用法:
    python3 qc_sample.py articles.jsonl [--per-lang 20] [--seed 42] [--out batch.json]

输入: JSONL，每行至少含 {"id", "language"} 字段（其余字段原样保留）。
输出: {"total": N, "languages": {lang: n}, "sample": [...]}
退出码: 0 正常; 2 输入错误
"""
import argparse
import collections
import json
import random
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--per-lang", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    by_lang = collections.defaultdict(list)
    try:
        with open(args.jsonl, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if "id" not in rec or "language" not in rec:
                    print(f"LINE {i}: 缺少 id/language 字段", file=sys.stderr)
                    sys.exit(2)
                by_lang[rec["language"]].append(rec)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    rng = random.Random(args.seed)
    sample = []
    for lang in sorted(by_lang):
        recs = by_lang[lang]
        k = min(args.per_lang, len(recs))
        sample.extend(rng.sample(recs, k))

    result = {
        "total": len(sample),
        "languages": {lang: len(v) for lang, v in sorted(by_lang.items())},
        "sample": sample,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"written: {args.out} ({len(sample)} 篇, {len(by_lang)} 语种)")
    else:
        print(text)


if __name__ == "__main__":
    main()
