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
    llm_base_url: str | None    # OpenAI 兼容端点（vLLM/LiteLLM），None=LLM 级关闭
    llm_model: str
    llm_api_key_env: str | None  # 密钥环境变量名，不落明文
    llm_timeout: float
    llm_max_tokens: int         # 单次调用输出上限（成本控制）
    gdelt_timespan: str         # 受限降级聚合层回看窗口
    embed_base_url: str | None  # OpenAI 兼容 /embeddings 端点，None=聚类关闭
    embed_model: str
    embed_api_key_env: str | None
    organize_interval: int      # 聚类/事件整理循环间隔（秒）
    render_enabled: bool        # 渲染兜底总开关（Playwright 可选依赖）
    render_daily_budget: int    # 每日渲染次数上限（设计：渲染占比 ≤15%）
    auth_required: bool         # 多用户模式：写操作与敏感读需登录（默认自托管免登）
    registration_mode: str      # open | approval | invite（设计 5.3.1 三档开关）
    rate_limit_per_min: int     # 标准档限流（匿名/session，60–100 档，默认 100）
    rate_limit_api_key_per_min: int  # API Key 档限流（500–1000 档，默认 600）
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
        llm_base_url=env.get("GNR_LLM_BASE_URL"),
        llm_model=env.get("GNR_LLM_MODEL", "qwen3-8b"),
        llm_api_key_env=env.get("GNR_LLM_API_KEY_ENV"),
        llm_timeout=float(env.get("GNR_LLM_TIMEOUT", "60")),
        llm_max_tokens=int(env.get("GNR_LLM_MAX_TOKENS", "2048")),
        gdelt_timespan=env.get("GNR_GDELT_TIMESPAN", "24h"),
        embed_base_url=env.get("GNR_EMBED_BASE_URL"),
        embed_model=env.get("GNR_EMBED_MODEL", "bge-m3"),
        embed_api_key_env=env.get("GNR_EMBED_API_KEY_ENV"),
        organize_interval=int(env.get("GNR_ORGANIZE_INTERVAL", "300")),
        render_enabled=env.get("GNR_RENDER_ENABLED", "0") == "1",
        render_daily_budget=int(env.get("GNR_RENDER_DAILY_BUDGET", "500")),
        auth_required=env.get("GNR_AUTH_REQUIRED", "0") == "1",
        registration_mode=env.get("GNR_REGISTRATION_MODE", "approval"),
        rate_limit_per_min=int(env.get("GNR_RATE_LIMIT_PER_MIN", "100")),
        rate_limit_api_key_per_min=int(
            env.get("GNR_RATE_LIMIT_API_KEY_PER_MIN", "600")),
    )
