"""黄金样本回归工具测试：check 子命令离线重跑抽取链 + 逐字段 diff。"""
import importlib.util
import json
from pathlib import Path

import pytest

from app.cleaner import extract_article
from tests.conftest import ARTICLE_HTML

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "golden_regression", REPO_ROOT / "scripts/golden_regression.py")
golden = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(golden)

ARTICLE_URL = "https://example.com/news/story-one"


def _make_golden_dir(tmp_path, corrupt: bool = False) -> Path:
    """用合成样本构造一个黄金样本目录（manifest 由当前抽取链生成）。"""
    src_dir = tmp_path / "golden" / "demo-source"
    src_dir.mkdir(parents=True)
    (src_dir / "article-0.html").write_text(ARTICLE_HTML, encoding="utf-8")
    meta = extract_article(ARTICLE_HTML, ARTICLE_URL)
    body = meta["body"]
    if corrupt:
        body = (body or "") + "（被篡改的基线）"
    manifest = {
        "source_id": "demo-source",
        "recorded_at": "2026-06-01T00:00:00Z",
        "entries": [{
            "file": "article-0.html",
            "url": ARTICLE_URL,
            "fetched_at": "2026-06-01T00:00:00Z",
            "extracted": {"title": meta["title"], "body": body,
                          "published_at": meta["published_at"]},
            "extractor": meta["extractor"],
        }],
    }
    (src_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return src_dir.parent


def test_check_golden_all_pass(tmp_path):
    golden_dir = _make_golden_dir(tmp_path)
    report = golden.check_golden(golden_dir)
    assert report["total"] == 1 and report["passed"] == 1
    assert report["success_rate"] == 1.0
    assert report["sources"][0]["source"] == "demo-source"
    assert report["sources"][0]["pass_rate"] == 1.0
    assert report["failures"] == []


def test_check_golden_field_diff_detected(tmp_path):
    golden_dir = _make_golden_dir(tmp_path, corrupt=True)
    report = golden.check_golden(golden_dir)
    assert report["passed"] == 0 and report["total"] == 1
    assert report["success_rate"] == 0.0
    assert len(report["failures"]) == 1
    fail = report["failures"][0]
    assert fail["source"] == "demo-source" and fail["field"] == "body"
    assert fail["url"] == ARTICLE_URL
    assert "expected" in fail and "actual" in fail


def test_check_golden_missing_html_file(tmp_path):
    golden_dir = _make_golden_dir(tmp_path)
    (golden_dir / "demo-source" / "article-0.html").unlink()
    report = golden.check_golden(golden_dir)
    assert report["passed"] == 0
    assert report["failures"][0]["field"] == "file"


def test_check_golden_empty_dir(tmp_path):
    report = golden.check_golden(tmp_path / "不存在")
    assert report["total"] == 0 and report["success_rate"] is None


def test_check_cli_json_output(tmp_path, capsys, monkeypatch):
    golden_dir = _make_golden_dir(tmp_path)
    monkeypatch.setattr("sys.argv", ["golden_regression.py", "check",
                                     "--golden-dir", str(golden_dir), "--json"])
    golden.main()
    out = json.loads(capsys.readouterr().out)
    # --json 输出包含总体成功率与每源通过率
    assert out["success_rate"] == 1.0
    assert out["sources"][0]["pass_rate"] == 1.0
    assert out["total"] == out["passed"] == 1


def test_check_cli_exit_code_below_threshold(tmp_path, monkeypatch):
    golden_dir = _make_golden_dir(tmp_path, corrupt=True)
    monkeypatch.setattr("sys.argv", ["golden_regression.py", "check",
                                     "--golden-dir", str(golden_dir)])
    with pytest.raises(SystemExit) as exc:
        golden.main()
    assert exc.value.code == 1
