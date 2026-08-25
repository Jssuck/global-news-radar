"""运行配置：环境变量 + 可选 .env 文件（简单 KEY=VALUE 解析，避免引入额外依赖）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """极简 .env 加载：仅支持 KEY=VALUE 行，不覆盖已有环境变量。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Settings:
    db_path: str
    poll_interval: int          # 默认轮询间隔（秒）
    max_articles_per_fetch: int
    request_timeout: float
    proxy_config: str
    disable_poller: bool
    max_concurrency: int        # 轮询并发上限（asyncio 信号量）
    per_host_min_interval: float  # 同 host 两次请求最小间隔（秒，礼貌限速）
    user_agent: str = "GlobalNewsRadar-MVP/0.1 (+https://github.com/Jssuck/global-news-radar)"


def get_settings() -> Settings:
    """每次调用重新读取环境，便于测试注入不同配置。"""
    load_dotenv(BASE_DIR / ".env")
    env = os.environ
    return Settings(
        db_path=env.get("GNR_DB_PATH", "data/gnr.db"),
        poll_interval=int(env.get("GNR_POLL_INTERVAL", "600")),
        max_articles_per_fetch=int(env.get("GNR_MAX_ARTICLES_PER_FETCH", "20")),
        request_timeout=float(env.get("GNR_REQUEST_TIMEOUT", "15")),
        proxy_config=env.get("GNR_PROXY_CONFIG", "config/proxies.yaml"),
        disable_poller=env.get("GNR_DISABLE_POLLER", "0") == "1",
        max_concurrency=int(env.get("GNR_MAX_CONCURRENCY", "16")),
        per_host_min_interval=float(env.get("GNR_PER_HOST_MIN_INTERVAL", "2")),
    )
