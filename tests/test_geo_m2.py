"""M2a geo 中信号判定与 API 端点测试。"""
from __future__ import annotations


def test_evaluate_geo_redirect_medium_signal():
    """重定向到地域警告页 → geo_suspected（中信号，不直接判受限）。"""
    from app.geo import evaluate_response
    v = evaluate_response(
        200, "<html>ok</html>",
        final_url="https://news.example.com/geo-block/notice",
        request_url="https://news.example.com/feed")
    assert v.verdict == "geo_suspected"
    assert "geo_redirect" in v.signals


def test_evaluate_normal_redirect_ok():
    """普通跳转（非地域警告）不触发中信号。"""
    from app.geo import evaluate_response
    v = evaluate_response(
        200, "<html>ok</html>",
        final_url="https://www.example.com/news/feed",
        request_url="https://example.com/feed")
    assert v.verdict == "ok"


def test_evaluate_truncation_medium_signal():
    """正文长度骤降 ≥90%（相对历史中位数）→ geo_suspected。"""
    from app.geo import evaluate_truncation
    history = [5000, 4800, 5200, 5100, 4900, 5000]
    assert evaluate_truncation(300, history).verdict == "geo_suspected"
    assert evaluate_truncation(300, history).signals == ["body_truncation"]
    # 样本不足 / 正常长度 → None
    assert evaluate_truncation(300, [5000, 4800]) is None
    assert evaluate_truncation(4500, history) is None


def test_confirmed_by_contrast():
    """直连失败 + 他国出口成功 → 确认；其余组合不确认。"""
    from app.geo import confirmed_by_contrast
    ok, region, _ = confirmed_by_contrast(False, True, "jp")
    assert ok and region == "jp"
    ok, region, _ = confirmed_by_contrast(True, False, "jp")
    assert not ok and region is None
    ok, region, _ = confirmed_by_contrast(False, False, "jp")
    assert not ok and region is None


def test_geo_hints_api(client):
    """geo-hints 列表与 resolve 端点（写入 app 实际使用的库）。"""
    from app.db import WRITE_LOCK, connect
    from app.pipeline import utcnow

    app_db = client.app.state.settings.db_path
    with WRITE_LOCK, connect(app_db) as conn:
        src = conn.execute("SELECT id FROM sources WHERE source_key='bbc-news'"
                           ).fetchone()
        conn.execute(
            "INSERT INTO geo_hints (source_id, required_region, evidence,"
            " status, created_at) VALUES (?, 'gb', 'HTTP 451', 'open', ?)",
            (src["id"], utcnow()))

    resp = client.get("/api/v1/geo-hints")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["required_region"] == "gb"
    assert "需要添加" in items[0]["hint"]

    resp = client.post(f"/api/v1/geo-hints/{items[0]['id']}/resolve")
    assert resp.status_code == 200 and resp.json()["status"] == "resolved"
    # resolve 后源回到待复检
    with connect(app_db) as conn:
        s = conn.execute("SELECT geo_status FROM sources WHERE id=?",
                         (src["id"],)).fetchone()
    assert s["geo_status"] == "unknown"


def test_proxy_profiles_api(client):
    """proxy-profiles CRUD：凭据不返回、重复 409、有绑定拒绝删除。"""
    resp = client.post("/api/v1/proxy-profiles", json={
        "profile_key": "us-dc-1", "type": "datacenter", "country": "us",
        "endpoint": "http://10.9.9.9:8080", "credentials_env": "GNR_X"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["credentials_env"] == "GNR_X"
    assert "pass" not in str(body).lower()  # 凭据值不出现于响应

    assert client.post("/api/v1/proxy-profiles", json={
        "profile_key": "us-dc-1", "type": "datacenter", "country": "us",
        "endpoint": "http://x"}).status_code == 409

    # 绑定后删除被拒
    b = client.post("/api/v1/proxy-bindings", json={
        "scope": "global", "proxy_profile_id": body["id"], "priority": 5})
    assert b.status_code == 201
    assert client.delete(
        f"/api/v1/proxy-profiles/{body['id']}").status_code == 409
    assert client.delete(
        f"/api/v1/proxy-bindings/{b.json()['id']}").status_code == 200
    assert client.delete(
        f"/api/v1/proxy-profiles/{body['id']}").status_code == 200


def test_proxy_binding_scope_validation(client):
    resp = client.post("/api/v1/proxy-profiles", json={
        "profile_key": "jp-1", "type": "residential", "country": "jp",
        "endpoint": "http://10.8.8.8:8080"})
    pid = resp.json()["id"]
    # scope=country 缺 country_code → 422
    assert client.post("/api/v1/proxy-bindings", json={
        "scope": "country", "proxy_profile_id": pid}).status_code == 422
    # scope=source 需存在的 source_id
    assert client.post("/api/v1/proxy-bindings", json={
        "scope": "source", "source_id": 999999,
        "proxy_profile_id": pid}).status_code == 422
