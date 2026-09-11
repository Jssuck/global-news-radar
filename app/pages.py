"""简易页面（Jinja2 模板）：/ 新闻流 与 /sources 源状态看板。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .db import connect
from .geo import geo_hint

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
    """源状态看板：受限/疑似受限源提示 + 三级代理绑定状态展示。"""
    settings = request.app.state.settings
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.*,
                   (SELECT MAX(fetched_at) FROM fetch_log f WHERE f.source_id = s.id)
                       AS last_log_at,
                   (SELECT COUNT(*) FROM articles a WHERE a.source_id = s.id)
                       AS article_count,
                   (SELECT p.profile_key FROM proxy_bindings b
                    JOIN proxy_profiles p ON p.id = b.proxy_profile_id
                    WHERE b.scope = 'source' AND b.source_id = s.id
                    ORDER BY b.priority LIMIT 1) AS bound_proxy
            FROM sources s ORDER BY s.geo_status IN ('geo_restricted','geo_suspected') DESC,
                                    s.country, s.name
            """
        ).fetchall()
        hints = conn.execute(
            """
            SELECT h.*, s.name AS source_name FROM geo_hints h
            JOIN sources s ON s.id = h.source_id
            WHERE h.status IN ('open','suspected') ORDER BY h.created_at DESC
            """
        ).fetchall()
        profiles = conn.execute(
            "SELECT * FROM proxy_profiles ORDER BY id").fetchall()
        bindings = conn.execute(
            """
            SELECT b.*, p.profile_key, s.source_key FROM proxy_bindings b
            JOIN proxy_profiles p ON p.id = b.proxy_profile_id
            LEFT JOIN sources s ON s.id = b.source_id
            ORDER BY b.scope, b.priority
            """).fetchall()
    source_list = []
    for r in rows:
        src = dict(r)
        status = src["geo_status"]
        src["hint"] = (
            geo_hint(src["required_region"], src["geo_evidence"],
                     suspected=(status == "geo_suspected"))
            if status in ("geo_restricted", "geo_suspected") else None
        )
        source_list.append(src)
    proxy_cfg = {"profiles": [dict(p) for p in profiles],
                 "bindings": [dict(b) for b in bindings],
                 "is_example": False}
    return templates.TemplateResponse(
        request, "sources.html",
        {"sources": source_list, "proxy_cfg": proxy_cfg,
         "geo_hints": [dict(h) for h in hints]},
    )
