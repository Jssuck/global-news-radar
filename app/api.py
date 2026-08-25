"""REST API v1（MVP）：sources / articles / stats / 手动触发抓取。

对外输出遵循合规红线：文章列表与详情仅返回 标题 + 短摘要 + 原文链接 + 元数据，
不提供正文全文（body 字段仅本地处理用）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from . import metrics
from .db import connect
from .geo import geo_hint
from .pipeline import fetch_source

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _source_dict(row) -> dict:
    src = dict(row)
    # 受限源附提示文案（设计 3.3.2 的 MVP 文字版）
    src["hint"] = (
        geo_hint(src["required_region"], src["geo_evidence"])
        if src["geo_status"] == "geo_restricted" else None
    )
    return src


@router.get("/sources")
def list_sources(request: Request, country: str | None = None,
                 geo_status: str | None = None):
    """源列表（含 geo_status 与 hint 字段），可按国家 / 受限状态过滤。"""
    sql = "SELECT * FROM sources WHERE 1=1"
    params: list = []
    if country:
        sql += " AND country = ?"
        params.append(country)
    if geo_status:
        sql += " AND geo_status = ?"
        params.append(geo_status)
    sql += " ORDER BY country, name"
    with connect(request.app.state.settings.db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return {"total": len(rows), "items": [_source_dict(r) for r in rows]}


@router.get("/articles")
def list_articles(request: Request,
                  page: int = Query(1, ge=1),
                  size: int = Query(20, ge=1, le=100),
                  source_id: int | None = None,
                  country: str | None = None,
                  language: str | None = None,
                  q: str | None = None):
    """文章列表：分页 + 按源/国家/语种过滤 + q 标题摘要 LIKE 检索。"""
    where, params = [], []
    if source_id:
        where.append("a.source_id = ?")
        params.append(source_id)
    if country:
        where.append("s.country = ?")
        params.append(country)
    if language:
        where.append("a.language = ?")
        params.append(language)
    if q:
        where.append("(a.title LIKE ? OR a.summary LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    base = f"FROM articles a JOIN sources s ON s.id = a.source_id {clause}"
    with connect(request.app.state.settings.db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT a.id, a.source_id, s.name AS source_name, s.country,
                   a.url, a.title, a.summary, a.language,
                   a.published_at, a.fetched_at
            {base}
            ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
            LIMIT ? OFFSET ?
            """,
            [*params, size, (page - 1) * size],
        ).fetchall()
    return {"total": total, "page": page, "size": size,
            "items": [dict(r) for r in rows]}


@router.get("/articles/{article_id}")
def get_article(request: Request, article_id: int):
    """文章详情：同样只返回短摘要与链接，不返回全文 body。"""
    with connect(request.app.state.settings.db_path) as conn:
        row = conn.execute(
            """
            SELECT a.id, a.source_id, s.name AS source_name, s.country,
                   a.url, a.title, a.summary, a.language,
                   a.published_at, a.fetched_at
            FROM articles a JOIN sources s ON s.id = a.source_id
            WHERE a.id = ?
            """,
            (article_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="文章不存在")
    return dict(row)


@router.post("/sources/{source_id}/check")
async def check_source(request: Request, source_id: int):
    """手动触发一次抓取（用于调试与新源验证）。"""
    settings = request.app.state.settings
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM sources WHERE id = ?",
                           (source_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="源不存在")
    result = await fetch_source(settings.db_path, dict(row), settings)
    return result


@router.get("/stats")
def stats(request: Request):
    """概览统计：源数 / 文章数 / 受限源数 / 最近抓取时间 + M1 管线指标。"""
    with connect(request.app.state.settings.db_path) as conn:
        sources_total = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        articles_total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        restricted = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE geo_status = 'geo_restricted'"
        ).fetchone()[0]
        last_fetch = conn.execute(
            "SELECT MAX(fetched_at) FROM fetch_log"
        ).fetchone()[0]
        by_country = conn.execute(
            "SELECT country, COUNT(*) AS n FROM sources GROUP BY country ORDER BY n DESC"
        ).fetchall()
        # M1 指标：抽取成功率 / 发现成功率 / 哈希去重命中（口径见 app.metrics）
        ext_ok, ext_total = metrics.extraction_stats(conn)
        disc_ok, disc_total = metrics.discovery_stats(conn)
        dedup = metrics.dedup_hits(conn)
    return {
        "sources_total": sources_total,
        "articles_total": articles_total,
        "geo_restricted_sources": restricted,
        "last_fetch_at": last_fetch,
        "sources_by_country": {r["country"]: r["n"] for r in by_country},
        "extraction_success_rate": (ext_ok / ext_total) if ext_total else None,
        "extraction_ok": ext_ok,
        "extraction_total": ext_total,
        "rss_discovery_ok": disc_ok,
        "rss_discovery_total": disc_total,
        "dedup_hits": dedup,
    }
