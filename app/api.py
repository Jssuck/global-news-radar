"""REST API v1（MVP）：sources / articles / stats / 手动触发抓取。

对外输出遵循合规红线：文章列表与详情仅返回 标题 + 短摘要 + 原文链接 + 元数据，
不提供正文全文（body 字段仅本地处理用）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import metrics
from .db import WRITE_LOCK, connect
from .geo import geo_hint
from .pipeline import fetch_source, utcnow
from .proxyconf import create_binding, create_profile

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _source_dict(row) -> dict:
    src = dict(row)
    status = src["geo_status"]
    # 受限/疑似受限源附提示文案（设计 3.3.2）
    src["hint"] = (
        geo_hint(src["required_region"], src["geo_evidence"],
                 suspected=(status == "geo_suspected"))
        if status in ("geo_restricted", "geo_suspected") else None
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


# ---- M2a：地域受限提示 + BYO 代理配置 ----


@router.get("/geo-hints")
def list_geo_hints(request: Request, status: str | None = None,
                   country: str | None = None):
    """地域受限提示列表（设计 5.4：按国家/状态过滤），联源名与国家。"""
    where, params = [], []
    if status:
        where.append("h.status = ?")
        params.append(status)
    if country:
        where.append("s.country = ?")
        params.append(country)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with connect(request.app.state.settings.db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT h.*, s.source_key, s.name AS source_name, s.country
            FROM geo_hints h JOIN sources s ON s.id = h.source_id
            {clause} ORDER BY h.created_at DESC
            """,
            params,
        ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["hint"] = geo_hint(item["required_region"], item["evidence"],
                                suspected=(item["status"] == "suspected"))
        items.append(item)
    return {"total": len(items), "items": items}


@router.post("/geo-hints/{hint_id}/resolve")
def resolve_geo_hint(request: Request, hint_id: int):
    """标记提示已处理（用户配置代理后联动）；源恢复待复检状态。"""
    now = utcnow()
    with WRITE_LOCK, connect(request.app.state.settings.db_path) as conn:
        hint = conn.execute("SELECT * FROM geo_hints WHERE id = ?",
                            (hint_id,)).fetchone()
        if not hint:
            raise HTTPException(status_code=404, detail="提示不存在")
        conn.execute(
            "UPDATE geo_hints SET status='resolved', resolved_at=? WHERE id=?",
            (now, hint_id),
        )
        # 源回到 unknown：下一轮抓取（经新绑定代理）重新判定
        conn.execute(
            "UPDATE sources SET geo_status='unknown' WHERE id = ?",
            (hint["source_id"],),
        )
    return {"id": hint_id, "status": "resolved", "resolved_at": now}


class ProxyProfileIn(BaseModel):
    profile_key: str = Field(min_length=1, max_length=64)
    type: str = Field(pattern="^(datacenter|residential|isp|mobile)$")
    country: str = Field(min_length=2, max_length=2)
    endpoint: str = Field(min_length=1)
    credentials_env: str | None = None
    provider: str | None = None
    notes: str | None = None


def _profile_out(row) -> dict:
    """凭据引用只返回 env 变量名，绝不返回凭据值（设计 3.3.3）。"""
    d = dict(row)
    d["credentials_env_set"] = bool(d.get("credentials_env"))
    return d


@router.get("/proxy-profiles")
def list_proxy_profiles(request: Request):
    """代理配置列表（凭据不返回，仅 env 变量名引用）。"""
    with connect(request.app.state.settings.db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM proxy_profiles ORDER BY id").fetchall()
    return {"total": len(rows), "items": [_profile_out(r) for r in rows]}


@router.post("/proxy-profiles", status_code=201)
def add_proxy_profile(request: Request, payload: ProxyProfileIn):
    """创建代理配置（BYO）；profile_key 重复返回 409。"""
    with connect(request.app.state.settings.db_path) as conn:
        dup = conn.execute("SELECT 1 FROM proxy_profiles WHERE profile_key = ?",
                           (payload.profile_key,)).fetchone()
    if dup:
        raise HTTPException(status_code=409, detail="profile_key 已存在")
    try:
        return _profile_out(create_profile(
            request.app.state.settings.db_path,
            payload.model_dump(), utcnow()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/proxy-profiles/{profile_id}")
def delete_proxy_profile(request: Request, profile_id: int):
    """删除代理配置；仍有绑定引用时 409 拒绝（级联检查）。"""
    with WRITE_LOCK, connect(request.app.state.settings.db_path) as conn:
        bound = conn.execute(
            "SELECT COUNT(*) FROM proxy_bindings WHERE proxy_profile_id = ?",
            (profile_id,)).fetchone()[0]
        if bound:
            raise HTTPException(
                status_code=409,
                detail=f"仍有 {bound} 条绑定引用该配置，请先解除绑定")
        cur = conn.execute("DELETE FROM proxy_profiles WHERE id = ?",
                           (profile_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="配置不存在")
    return {"deleted": profile_id}


class ProxyBindingIn(BaseModel):
    scope: str = Field(pattern="^(source|country|global)$")
    source_id: int | None = None
    country_code: str | None = None
    proxy_profile_id: int
    priority: int = 100


@router.get("/proxy-bindings")
def list_proxy_bindings(request: Request):
    """绑定列表（源级/国家级/全局三级）。"""
    with connect(request.app.state.settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT b.*, p.profile_key, p.country AS proxy_country,
                   s.source_key
            FROM proxy_bindings b
            JOIN proxy_profiles p ON p.id = b.proxy_profile_id
            LEFT JOIN sources s ON s.id = b.source_id
            ORDER BY b.scope, b.priority
            """).fetchall()
    return {"total": len(rows), "items": [dict(r) for r in rows]}


@router.post("/proxy-bindings", status_code=201)
def add_proxy_binding(request: Request, payload: ProxyBindingIn):
    """创建绑定（scope 必填字段与 profile 存在性校验）。"""
    try:
        return create_binding(request.app.state.settings.db_path,
                              payload.model_dump(), utcnow())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/proxy-bindings/{binding_id}")
def delete_proxy_binding(request: Request, binding_id: int):
    with WRITE_LOCK, connect(request.app.state.settings.db_path) as conn:
        cur = conn.execute("DELETE FROM proxy_bindings WHERE id = ?",
                           (binding_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="绑定不存在")
    return {"deleted": binding_id}


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
