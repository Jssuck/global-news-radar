"""M2-F1 地域受限判定黄金集回归：≥50 标注样本，准确率 ≥95%，
且 429/5xx 误判为 geo_restricted 必须为 0。"""
from __future__ import annotations

import json
from pathlib import Path

from app.geo import evaluate_response, evaluate_truncation

GOLDEN = (Path(__file__).parent / "fixtures" / "geo_golden.json")


def _run(case: dict) -> str:
    if case["kind"] == "response":
        v = evaluate_response(
            case.get("status"), case.get("body", ""),
            final_url=case.get("final_url"),
            request_url=case.get("request_url"))
        return v.verdict
    v = evaluate_truncation(case["body_len"], case["history"])
    return v.verdict if v else "ok"


def test_geo_golden_accuracy():
    cases = json.loads(GOLDEN.read_text())["cases"]
    assert len(cases) >= 50, "M2-F1 要求 ≥50 个标注样本"
    misses = []
    anti_bot_to_geo = []
    for c in cases:
        got = _run(c)
        if got != c["expected"]:
            misses.append((c["name"], c["expected"], got))
        if c.get("status") in (429, 500, 502, 503) and got == "geo_restricted":
            anti_bot_to_geo.append(c["name"])
    accuracy = 1 - len(misses) / len(cases)
    assert not anti_bot_to_geo, f"429/5xx 被误判为地域受限: {anti_bot_to_geo}"
    assert accuracy >= 0.95, (
        f"准确率 {accuracy:.1%} < 95%，误判样本: {misses}")
