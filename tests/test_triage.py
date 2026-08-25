"""scripts/triage_sources.py 分诊逻辑与 loader active 标志的回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from triage_sources import classify

from app.db import connect, init_db
from app.sources_loader import load_seed_sources


def _write_source(dir_path: Path, key: str, **over) -> Path:
    doc = {
        "name": key, "base_url": f"https://{key}.example.com", "country": "us",
        "language": "en", "media_type": "newspaper", "influence_tier": "national",
        "discovery": {"strategy": "rss", "feed_url": f"https://{key}.example.com/rss"},
        "geo": {"status": "ok"},
        "update_profile": {"estimated_interval": "PT10M"},
        "legal": {"robots_checked": True}, "maintainers": [],
    }
    doc.update(over)
    p = dir_path / f"{key}.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return p


class TestClassify:
    def test_ok_rounds_not_deactivated(self):
        assert classify(["ok", "error"], 2)[:2] == ("ok", False)
        assert classify(["not_modified"], 2)[:2] == ("ok", False)

    def test_persistent_robots_blocked_deactivated(self):
        verdict, deact, _ = classify(["robots_blocked", "robots_blocked"], 2)
        assert (verdict, deact) == ("blocked_legal", True)

    def test_persistent_anti_bot_deactivated(self):
        verdict, deact, _ = classify(["anti_bot", "anti_bot"], 2)
        assert (verdict, deact) == ("anti_bot", True)

    def test_mixed_error_then_anti_bot_uses_latest(self):
        verdict, deact, _ = classify(["anti_bot", "error"], 2)
        assert (verdict, deact) == ("anti_bot", True)

    def test_network_error_inconclusive_kept_active(self):
        verdict, deact, _ = classify(["error", "error"], 2)
        assert (verdict, deact) == ("inconclusive", False)

    def test_insufficient_rounds_inconclusive(self):
        verdict, deact, reason = classify(["robots_blocked"], 2)
        assert (verdict, deact) == ("inconclusive", False)
        assert "轮次不足" in reason


class TestLoaderActiveFlag:
    def test_active_false_roundtrip(self, tmp_path):
        _write_source(tmp_path, "src-a", active=False,
                      triage={"verdict": "blocked_legal",
                              "evidence": "连续 2 轮 robots_blocked",
                              "date": "2026-08-25", "revisit": "manual"})
        _write_source(tmp_path, "src-b")  # 缺省 active
        db = tmp_path / "t.db"
        init_db(str(db))
        load_seed_sources(str(db), tmp_path)
        with connect(str(db)) as conn:
            rows = {r["source_key"]: r["active"]
                    for r in conn.execute("SELECT source_key, active FROM sources")}
        assert rows == {"src-a": 0, "src-b": 1}

    def test_reactivation_propagates_on_upsert(self, tmp_path):
        _write_source(tmp_path, "src-a", active=False,
                      triage={"verdict": "anti_bot", "evidence": "x",
                              "date": "2026-08-25", "revisit": "M2"})
        db = tmp_path / "t.db"
        init_db(str(db))
        load_seed_sources(str(db), tmp_path)
        # M2 恢复：去掉 active/triage 后重新加载应回到 active=1
        _write_source(tmp_path, "src-a")
        load_seed_sources(str(db), tmp_path)
        with connect(str(db)) as conn:
            active = conn.execute(
                "SELECT active FROM sources WHERE source_key='src-a'").fetchone()[0]
        assert active == 1
