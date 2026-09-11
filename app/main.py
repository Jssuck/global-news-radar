"""FastAPI 应用入口：初始化存储、加载种子源、启动后台轮询。

启动方式：uvicorn app.main:app --reload  →  http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router as api_router
from .config import BASE_DIR, get_settings
from .db import init_db
from .pages import router as pages_router
from .poller import poll_loop
from .sources_loader import load_seed_sources

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    init_db(settings.db_path)
    n = load_seed_sources(settings.db_path, BASE_DIR / "sources")
    logging.getLogger("gnr").info("loaded %s seed sources", n)
    # BYO 代理：config/proxies.yaml → DB（示例文件不导入）
    from .pipeline import utcnow
    from .proxyconf import sync_proxy_config
    imported = sync_proxy_config(settings.db_path, settings.proxy_config, utcnow())
    if imported:
        logging.getLogger("gnr").info("imported %s proxy profiles", imported)

    stop_event = asyncio.Event()
    task = None
    if not settings.disable_poller:
        task = asyncio.create_task(poll_loop(settings.db_path, settings, stop_event))
    yield
    stop_event.set()
    if task:
        await task


app = FastAPI(
    title="Global News Radar (MVP)",
    description="全球新闻媒体聚合监控平台 MVP：RSS 轮询 + 一级规则清洗 + 地域受限检测提示",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)
app.include_router(pages_router)
