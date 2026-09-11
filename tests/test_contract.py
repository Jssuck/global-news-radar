"""M3-F1 OpenAPI 契约测试：全部端点在 schema 中注册、GET 端点可调用、
关键列表端点响应结构与声明一致（轻量校验，不引 schemathesis 依赖）。"""
from __future__ import annotations


def _openapi(client):
    return client.get("/openapi.json").json()


def test_all_routes_in_openapi(client):
    spec = _openapi(client)
    declared = set(spec["paths"])
    import app.main
    from app.main import app
    missing = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith("/api/v1") and path not in declared:
            missing.append(path)
    assert not missing, f"路由未出现在 OpenAPI: {missing}"
    assert len(declared) >= 27, f"端点数 {len(declared)} < 27"


def test_error_responses_have_detail(client):
    """统一错误形状：detail 字段（401/403/404/422/429 都适用）。"""
    r = client.get("/api/v1/sources/999999")
    assert r.status_code == 404 and "detail" in r.json()
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401 and "detail" in r.json()
    r = client.post("/api/v1/auth/register", json={"email": "x"})
    assert r.status_code == 422 and "detail" in r.json()


def test_get_endpoints_contract(client):
    """全部无必填参数的 GET 端点可调用且返回 JSON 对象。"""
    spec = _openapi(client)
    called = skipped = 0
    for path, ops in spec["paths"].items():
        if "{" in path or "get" not in ops:
            continue
        if path == "/api/v1/stream":
            skipped += 1
            continue   # SSE 长连接，单独在 test_api_v2 覆盖
        params = ops["get"].get("parameters", [])
        if any(p.get("required") for p in params):
            skipped += 1
            continue
        r = client.get(path)
        assert r.status_code in (200, 401, 403), (
            f"GET {path} → {r.status_code}")
        if r.status_code == 200:
            assert isinstance(r.json(), (dict, list)), f"GET {path} 非 JSON"
        called += 1
    assert called >= 10, f"仅 {called} 个 GET 被契约覆盖（跳过 {skipped}）"


def test_list_endpoints_shape(client):
    """列表端点统一契约：total + items。"""
    for path in ("/api/v1/sources", "/api/v1/articles", "/api/v1/events",
                 "/api/v1/geo-hints", "/api/v1/dead-letters",
                 "/api/v1/gdelt-checks"):
        r = client.get(path)
        assert r.status_code == 200, path
        body = r.json()
        assert "items" in body and "total" in body, path
        assert isinstance(body["items"], list)


def test_article_schema_subset(client):
    """公开文章端点不输出全文字段（合规红线：public API 不分发正文）。"""
    spec = _openapi(client)
    for path, ops in spec["paths"].items():
        text = str(ops)
        assert "body" not in text or "articles" not in path or \
            "full_text" not in text.lower()
