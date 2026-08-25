"""种子源 schema 校验：sources/*.yaml 必须通过项目 skill 的 validate_source.py。

直接复用 skills/news-source-onboarding/scripts/validate_source.py 的 check 函数，
保证种子库与源接入规范一致（离线测试，仅读文件）。
"""
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "validate_source",
    REPO_ROOT / "skills/news-source-onboarding/scripts/validate_source.py")
validate_source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_source)

SOURCE_FILES = sorted((REPO_ROOT / "sources").glob("*.yaml"))


def test_seed_source_count_and_coverage():
    """≥12 个源、≥6 个国家、≥3 种语言。"""
    import yaml

    docs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in SOURCE_FILES]
    assert len(docs) >= 12
    assert len({d["country"] for d in docs}) >= 6
    assert len({d["language"] for d in docs}) >= 3


@pytest.mark.parametrize("path", SOURCE_FILES, ids=[p.stem for p in SOURCE_FILES])
def test_source_yaml_passes_skill_validation(path):
    errors, _warnings = validate_source.check(validate_source.load(str(path)))
    assert not errors, f"{path.name}: {errors}"
