"""地域代理配置读取（MVP 只读展示，不做真实代理转发）。

对应设计 3.3.3 的 BYO Proxy 模型：profiles 定义出口，bindings 按
源级 > 国家级 > 全局 三级绑定。MVP 仅把配置读出展示在 /sources 页面。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .config import BASE_DIR


def load_proxy_config(config_path: str) -> dict:
    """加载代理配置；用户配置不存在时回退到示例文件。"""
    path = Path(config_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    fallback = False
    if not path.is_file():
        path = BASE_DIR / "config" / "proxies.example.yaml"
        fallback = True
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"profiles": [], "bindings": [], "is_example": True,
                "error": "配置文件解析失败"}
    return {
        "profiles": doc.get("profiles") or [],
        "bindings": doc.get("bindings") or [],
        "is_example": fallback,
        "path": str(path),
    }
