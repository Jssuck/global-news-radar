"""M3a 认证 / 注册审核 / RBAC / API Key / 限流测试。"""
from __future__ import annotations

import pytest
from starlette.requests import Request


def test_password_hash_roundtrip():
    from app.auth import hash_password, verify_password
    h = hash_password("secret-pass-123")
    assert verify_password("secret-pass-123", h)
    assert not verify_password("wrong", h)
    assert "$" in h and "secret" not in h  # 不落明文


def test_register_bootstrap_first_admin(db_path):
    """首位注册用户自动 admin+approved（自托管引导）。"""
    from app.auth import register_user
    u = register_user(db_path, "admin@x.com", "password-123", None,
                      "2026-09-11T00:00:00")
    assert u["role"] == "admin" and u["status"] == "approved"
    u2 = register_user(db_path, "viewer@x.com", "password-123", None,
                       "2026-09-11T00:00:00")
    assert u2["status"] == "pending" and u2["role"] == "viewer"


def test_register_invite_code(db_path):
    """有效邀请码计次并记录；用尽/过期/不存在则不生效。"""
    from app.auth import create_invite, register_user
    now = "2026-09-11T00:00:00"
    register_user(db_path, "a@x.com", "password-123", None, now)  # admin
    create_invite(db_path, "INVITE-1", 1, 1, now)
    register_user(db_path, "b@x.com", "password-123", "INVITE-1", now)
    register_user(db_path, "c@x.com", "password-123", "INVITE-1", now)  # 用尽
    from app.db import connect
    with connect(db_path) as conn:
        inv = conn.execute("SELECT used_count FROM invite_codes").fetchone()
        c = conn.execute("SELECT invite_code FROM users WHERE email='c@x.com'"
                         ).fetchone()
    assert inv["used_count"] == 1 and c["invite_code"] is None


def test_registration_state_machine(db_path):
    """pending → approved/rejected 单向流转；终态不可再变。"""
    from app.auth import decide_registration, register_user
    now = "2026-09-11T00:00:00"
    admin = register_user(db_path, "a@x.com", "password-123", None, now)
    u = register_user(db_path, "u@x.com", "password-123", None, now)
    r = decide_registration(db_path, u["id"], True, admin["id"], now)
    assert r["status"] == "approved"
    # 终态不可再审核
    with pytest.raises(ValueError, match="pending"):
        decide_registration(db_path, u["id"], False, admin["id"], now)


def test_login_requires_approved(db_path):
    from app.auth import decide_registration, login_user, register_user
    now = "2026-09-11T00:00:00"
    admin = register_user(db_path, "a@x.com", "password-123", None, now)
    u = register_user(db_path, "u@x.com", "password-123", None, now)
    assert login_user(db_path, "u@x.com", "password-123", now) is None  # pending
    decide_registration(db_path, u["id"], True, admin["id"], now)
    res = login_user(db_path, "u@x.com", "password-123", now)
    assert res is not None and res[0].startswith("gnr_")
    # 密码错误 / rejected 同样拒绝
    assert login_user(db_path, "u@x.com", "bad", now) is None


def test_api_key_roundtrip_and_revoke(db_path):
    from app.auth import create_api_key, register_user, resolve_principal
    now = "2026-09-11T00:00:00"
    u = register_user(db_path, "a@x.com", "password-123", None, now)
    key = create_api_key(db_path, u["id"], "ci", "read,write", now)
    assert key.startswith("gnr_")

    req = Request({"type": "http", "method": "GET", "path": "/",
                   "headers": [(b"x-api-key", key.encode())],
                   "client": ("127.0.0.1", 0)})
    p = resolve_principal(db_path, req)
    assert p and p["email"] == "a@x.com" and "write" in p["_scopes"]

    from app.auth import revoke_api_key
    assert revoke_api_key(db_path, 1, u["id"], now)
    req2 = Request({"type": "http", "method": "GET", "path": "/",
                    "headers": [(b"x-api-key", key.encode())],
                    "client": ("127.0.0.1", 0)})
    assert resolve_principal(db_path, req2) is None  # 已吊销


def test_rbac_permissions(db_path):
    """RBAC：admin 全权；editor 可写不可管用户；viewer 只读。"""
    from app.auth import Principal
    admin = Principal({"role": "admin"}, "session", [])
    editor = Principal({"role": "editor"}, "session", [])
    viewer = Principal({"role": "viewer"}, "session", [])
    key_rw = Principal({"role": "viewer"}, "api_key", ["read", "write"])
    key_ro = Principal({"role": "viewer"}, "api_key", ["read"])
    assert admin.can("anything")
    assert editor.can("source:manage") and not editor.can("admin:users")
    assert viewer.can("read") and not viewer.can("write")
    assert key_rw.can("write") and not key_ro.can("write")


def test_rate_limiter():
    from app.auth import RateLimiter
    rl = RateLimiter(rate_per_min=3)
    assert all(rl.allow("k") for _ in range(3))
    assert not rl.allow("k")          # 第 4 次超限
    assert rl.allow("other")          # 不同 key 互不影响


def test_auth_endpoints_flow(client):
    """端到端：register → admin approve → login → me → api-key → 受限端点鉴权。"""
    # 引导管理员
    r = client.post("/api/v1/auth/register", json={
        "email": "root@x.com", "password": "password-123"})
    assert r.status_code == 201 and r.json()["role"] == "admin"
    r = client.post("/api/v1/auth/login", json={
        "email": "root@x.com", "password": "password-123"})
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").json()["role"] == "admin"

    # 新用户 pending → admin 审核通过 → 可登录
    r = client.post("/api/v1/auth/register", json={
        "email": "u@x.com", "password": "password-123"})
    uid = r.json()["id"]
    assert r.json()["status"] == "pending"
    assert client.post("/api/v1/auth/login", json={
        "email": "u@x.com", "password": "password-123"}).status_code == 401
    assert client.post(
        f"/api/v1/admin/registrations/{uid}/approve").status_code == 200
    assert client.post("/api/v1/auth/login", json={
        "email": "u@x.com", "password": "password-123"}).status_code == 200

    # API key 签发与吊销
    r = client.post("/api/v1/api-keys", json={"name": "ci", "scopes": "read"})
    assert r.status_code == 201
    key = r.json()["api_key"]
    client.cookies.clear()   # 去掉 session cookie 才能走 API-key 通道
    me = client.get("/api/v1/auth/me", headers={"X-API-Key": key})
    assert me.json()["via"] == "api_key"

    # 非 admin 访问审核队列被拒：重新登录 admin 审批 viewer，再以 viewer 访问
    client.post("/api/v1/auth/login", json={
        "email": "root@x.com", "password": "password-123"})
    r2 = client.post("/api/v1/auth/register", json={
        "email": "v@x.com", "password": "password-123"})
    vid = r2.json()["id"]
    assert client.post(
        f"/api/v1/admin/registrations/{vid}/approve").status_code == 200
    client.post("/api/v1/auth/login", json={
        "email": "v@x.com", "password": "password-123"})
    # viewer session 访问 admin 端点 → 403
    assert client.get("/api/v1/admin/registrations").status_code == 403
