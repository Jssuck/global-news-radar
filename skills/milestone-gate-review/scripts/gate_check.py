"""对照验收标准核对实测指标，输出逐项 PASS/FAIL 与总体结论。

用法:
    python3 gate_check.py --metrics m1_metrics.json --criteria m1_criteria.json [--json]

metrics 文件:   {"sources_onboarded": 320, "rss_discovery_rate": 0.87, ...}
criteria 文件:  [{"key": "sources_onboarded", "op": ">=", "target": 300, "critical": true}, ...]
op 支持: >= <= > < ==
退出码: 0 = 总体 PASS; 1 = 存在 critical 失败; 2 = 输入错误
"""
import argparse
import json
import operator
import sys

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--criteria", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.metrics, encoding="utf-8") as f:
            metrics = json.load(f)
        with open(args.criteria, encoding="utf-8") as f:
            criteria = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    rows, critical_fail = [], False
    for c in criteria:
        key, op, target = c["key"], c["op"], c["target"]
        critical = bool(c.get("critical", True))
        actual = metrics.get(key)
        if actual is None:
            ok = False
            note = "MISSING"
        else:
            ok = OPS[op](actual, target)
            note = ""
        if not ok and critical:
            critical_fail = True
        rows.append({"key": key, "op": op, "target": target, "actual": actual,
                     "critical": critical, "pass": ok, "note": note})

    verdict = "FAIL" if critical_fail else (
        "CONDITIONAL" if any(not r["pass"] for r in rows) else "PASS")
    if args.json:
        print(json.dumps({"verdict": verdict, "checks": rows}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            mark = "PASS" if r["pass"] else ("FAIL*" if r["critical"] else "fail")
            print(f"[{mark}] {r['key']}: {r['op']} {r['target']}  actual={r['actual']} {r['note']}")
        print(f"VERDICT: {verdict}")
    sys.exit(1 if critical_fail else 0)


if __name__ == "__main__":
    main()
