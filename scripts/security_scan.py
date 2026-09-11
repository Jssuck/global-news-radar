#!/usr/bin/env python3
"""M4-F3 安全自查：密钥泄漏扫描 + 依赖许可证报告。

用法：
    python scripts/security_scan.py            # 两项全跑，CI 可用
    python scripts/security_scan.py --secrets  # 仅密钥扫描
    python scripts/security_scan.py --licenses # 仅许可证报告

密钥扫描：对 git 跟踪文件做常见密钥模式匹配（排除 tests/fixtures 中的
合成样本与本脚本自身的模式定义）。命中即非零退出。

许可证：优先 pip-licenses；缺失时退回 importlib.metadata 粗查，
把已知 copyleft/非标许可证（GPL/AGPL/LGPL/SSPL/BUSL…）列为高危。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 常见密钥特征（宁误报不漏报；命中需人工复核）
SECRET_PATTERNS = {
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "aws_secret_key": r"(?i)aws(.{0,20})?['\"][0-9a-zA-Z/+]{40}['\"]",
    "generic_api_key": r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"\s]{16,}['\"]",
    "openai_key": r"sk-[a-zA-Z0-9]{20,}",
    "github_pat": r"gh[pousr]_[a-zA-Z0-9]{36,}",
    "private_key": r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY",
    "basic_auth_url": r"https?://[^/\s:]+:[^/\s@]+@",
}
# 误报白名单：测试夹具/文档中的占位写法与 ${ENV} 变量引用形式
ALLOWLIST_SUBSTR = [
    "credentials_env", "GNR_LLM_API_KEY_ENV", "sk-test", "gnr_x",
    "password-123", "example.com", "your-", "placeholder",
    "${", "user:pass@", "x-access-token",
]
# 文件级排除（合成样本/锁文件/本脚本）
EXCLUDE_GLOBS = ["tests/fixtures/*", "uv.lock", "poetry.lock",
                 "scripts/security_scan.py", "*.png", "*.jpg"]

# 常见 copyleft / 非标协议（对本项目 MIT 发行属高危或需审查）
RISKY_LICENSE = re.compile(
    r"(?i)GPL|AGPL|LGPL|SSPL|BUSL|Commons Clause|CC-BY-SA|unknown")

# 已人工核实的许可证覆盖（上游元数据缺失/非标写法时使用）
LICENSE_OVERRIDES = {
    "socksio": "MIT",   # encode/socksio，PyPI 元数据为 UNKNOWN，源码 LICENSE 为 MIT
}


def scan_licenses() -> int:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "piplicenses", "--format=json",
             "--with-system"], capture_output=True, text=True, check=False)
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
        pkgs = json.loads(out.stdout)
    except (RuntimeError, OSError, json.JSONDecodeError, FileNotFoundError):
        # 无 pip-licenses 时退回 importlib.metadata
        from importlib.metadata import distributions
        pkgs = [{"Name": d.metadata.get("Name", "?"),
                 "License": d.metadata.get("License",
                     d.metadata.get("Classifier", "")) or "unknown"}
                for d in distributions()]
    for p in pkgs:
        name = p.get("Name", "")
        if name.lower() in LICENSE_OVERRIDES:
            p["License"] = LICENSE_OVERRIDES[name.lower()]
    risky = [f"{p['Name']} ({p['License']})" for p in pkgs
             if RISKY_LICENSE.search(str(p["License"]))]
    print(f"许可证扫描：{len(pkgs)} 包，高危/待审 {len(risky)} 个")
    for r in risky:
        print("  高危/待审:", r)
    return len(risky)


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False)
    return [REPO / p for p in out.stdout.splitlines() if p.strip()]


def scan_secrets() -> int:
    hits = []
    for path in _tracked_files():
        rel = path.relative_to(REPO).as_posix()
        if any(path.match(g) for g in EXCLUDE_GLOBS):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pat in SECRET_PATTERNS.items():
            for m in re.finditer(pat, text):
                snippet = m.group(0)
                if any(w in snippet for w in ALLOWLIST_SUBSTR):
                    continue
                line = text[:m.start()].count("\n") + 1
                hits.append(f"{rel}:{line} [{name}] {snippet[:60]}")
    for h in hits:
        print("SECRET?", h)
    print(f"密钥扫描：{len(hits)} 个待复核命中")
    return len(hits)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", action="store_true")
    ap.add_argument("--licenses", action="store_true")
    args = ap.parse_args()
    do_all = not (args.secrets or args.licenses)
    fails = 0
    if args.secrets or do_all:
        fails += scan_secrets()
    if args.licenses or do_all:
        fails += scan_licenses()
    sys.exit(1 if fails else 0)
