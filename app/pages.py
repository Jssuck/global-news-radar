"""简易页面（Jinja2 模板）：/ 新闻流 与 /sources 源状态看板。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .db import connect
from .geo import geo_hint
from .proxyconf import load_proxy_config

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """新闻流列表：标题 + 来源 + 时间 + 前 200 字符摘要 + 原文链接（不展示全文）。"""
    with connect(request.app.state.settings.db_path) as conn:
        articles = conn.execute(
            """
            SELECT a.id, a.title, a.summary, a.url, a.language,
                   a.published_at, a.fetched_at, s.name AS source_name, s.country
            FROM articles a JOIN sources s ON s.id = a.source_id
            ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
            LIMIT 50
            """
        ).fetchall()
        stats = {
            "sources": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "articles": conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
        }
    return templates.TemplateResponse(
        request, "index.html",
        {"articles": [dict(a) for a in articles], "stats": stats},
    )


@router.get("/sources", response_class=HTMLResponse)
def sources_board(request: Request):
    """源状态看板：含受限源的地域代理提示与代理配置展示（只读）。"""
    settings = request.app.state.settings
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.*,
                   (SELECT MAX(fetched_at) FROM fetch_log f WHERE f.source_id = s.id)
                       AS last_log_at,
                   (SELECT COUNT(*) FROM articles a WHERE a.source_id = s.id)
                       AS article_count
            FROM sources s ORDER BY s.geo_status = 'geo_restricted' DESC,
                                    s.country, s.name
            """
        ).fetchall()
    source_list = []
    for r in rows:
        src = dict(r)
        src["hint"] = (
            geo_hint(src["required_region"], src["geo_evidence"])
            if src["geo_status"] == "geo_restricted" else None
        )
        source_list.append(src)
    proxy_cfg = load_proxy_config(settings.proxy_config)
    return templates.TemplateResponse(
        request, "sources.html",
        {"sources": source_list, "proxy_cfg": proxy_cfg},
    )
