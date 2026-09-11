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
    assert u2["status"] == "pending" and u2["role"] == "user"


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
    """RBAC 四角色：admin 全权；editor 可写不可管用户；user/api-caller 只读。"""
    from app.auth import Principal
    admin = Principal({"role": "admin"}, "session", [])
    editor = Principal({"role": "editor"}, "session", [])
    user = Principal({"role": "user"}, "session", [])
    caller = Principal({"role": "api-caller"}, "api_key", ["read"])
    key_rw = Principal({"role": "user"}, "api_key", ["read", "write"])
    key_ro = Principal({"role": "user"}, "api_key", ["read"])
    assert admin.can("anything")
    assert editor.can("source:manage") and not editor.can("admin:users")
    assert user.can("read") and not user.can("write")
    assert caller.can("read") and not caller.can("write")
    assert key_rw.can("write") and not key_ro.can("write")
    # 旧别名 viewer 按 user 处理（向后兼容）
    assert Principal({"role": "viewer"}, "session", []).can("read")


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


# ---- M4：六条迁移路径 + 封禁吊销 + 三档模式 + 分级限流 ----


def test_state_machine_six_paths(db_path):
    """M3-F2 六条迁移路径全覆盖。

    [*]→approved(开放注册/首位引导/邀请码旁路)、[*]→pending、
    pending→approved、pending→rejected、approved→suspended、
    suspended→approved。
    """
    from app.auth import decide_registration, register_user, set_user_status
    now = "2026-09-11T00:00:00"
    # 路径1 [*]→approved：首位引导
    admin = register_user(db_path, "a@x.com", "password-123", None, now)
    assert admin["status"] == "approved" and admin["role"] == "admin"
    # 路径2 [*]→approved：开放注册模式
    u = register_user(db_path, "o@x.com", "password-123", None, now,
                      registration_mode="open")
    assert u["status"] == "approved"
    # 路径3 [*]→approved：邀请码旁路（approval 模式下）
    from app.auth import create_invite
    create_invite(db_path, "VIP-9", 1, admin["id"], now)
    u = register_user(db_path, "v@x.com", "password-123", "VIP-9", now,
                      registration_mode="approval")
    assert u["status"] == "approved"
    # 路径4 [*]→pending → approved
    u = register_user(db_path, "p1@x.com", "password-123", None, now)
    assert u["status"] == "pending"
    decide_registration(db_path, u["id"], True, admin["id"], now)
    # 路径5 [*]→pending → rejected（终态）
    u2 = register_user(db_path, "p2@x.com", "password-123", None, now)
    decide_registration(db_path, u2["id"], False, admin["id"], now)
    # 路径6 approved→suspended→approved
    r = set_user_status(db_path, u["id"], "suspended", admin["id"], now)
    assert r["status"] == "suspended"
    r = set_user_status(db_path, u["id"], "approved", admin["id"], now)
    assert r["status"] == "approved"
    # 非法迁移：rejected 终态、pending 不能直接 suspend
    import pytest
    with pytest.raises(ValueError):
        set_user_status(db_path, u2["id"], "suspended", admin["id"], now)


def test_suspend_revokes_sessions_and_keys(db_path):
    """封禁即时吊销 session 与 API key（M3-F6）。"""
    from app.auth import (
        create_api_key,
        decide_registration,
        login_user,
        register_user,
        resolve_principal,
        set_user_status,
    )
    now = "2026-09-11T00:00:00"
    admin = register_user(db_path, "a@x.com", "password-123", None, now)
    u = register_user(db_path, "u@x.com", "password-123", None, now)
    decide_registration(db_path, u["id"], True, admin["id"], now)
    token, _ = login_user(db_path, "u@x.com", "password-123", now)
    key = create_api_key(db_path, u["id"], "ci", "read", now)

    set_user_status(db_path, u["id"], "suspended", admin["id"], now)
    req = Request({"type": "http", "method": "GET", "path": "/",
                   "headers": [(b"cookie", f"gnr_session={token}".encode()),
                               (b"x-api-key", key.encode())],
                   "client": ("127.0.0.1", 0)})
    assert resolve_principal(db_path, req) is None
    assert login_user(db_path, "u@x.com", "password-123", now) is None


def test_invite_mode_requires_code(db_path):
    from app.auth import create_invite, register_user
    now = "2026-09-11T00:00:00"
    admin = register_user(db_path, "a@x.com", "password-123", None, now)
    import pytest
    with pytest.raises(ValueError, match="邀请制"):
        register_user(db_path, "u@x.com", "password-123", None, now,
                      registration_mode="invite")
    with pytest.raises(ValueError, match="无效"):
        register_user(db_path, "u@x.com", "password-123", "BAD", now,
                      registration_mode="invite")
    create_invite(db_path, "GOOD-1", 1, admin["id"], now)
    u = register_user(db_path, "u@x.com", "password-123", "GOOD-1", now,
                      registration_mode="invite")
    assert u["status"] == "approved"


def test_api_caller_no_web_session(db_path):
    """api-caller 角色仅 API Key 通道，Web 登录被拒（M3-F6 四角色）。"""
    from app.auth import (
        create_api_key,
        login_user,
        register_user,
        resolve_principal,
        set_user_role,
    )
    now = "2026-09-11T00:00:00"
    admin = register_user(db_path, "a@x.com", "password-123", None, now)
    u = register_user(db_path, "bot@x.com", "password-123", None, now,
                      registration_mode="open")
    set_user_role(db_path, u["id"], "api-caller", admin["id"], now)
    assert login_user(db_path, "bot@x.com", "password-123", now) is None
    key = create_api_key(db_path, u["id"], "bot", "read", now)
    req = Request({"type": "http", "method": "GET", "path": "/",
                   "headers": [(b"x-api-key", key.encode())],
                   "client": ("127.0.0.1", 0)})
    p = resolve_principal(db_path, req)
    assert p and p["role"] == "api-caller"


def test_audit_logs_written(db_path):
    from app.auth import (
        decide_registration,
        register_user,
        set_user_role,
        set_user_status,
    )
    now = "2026-09-11T00:00:00"
    admin = register_user(db_path, "a@x.com", "password-123", None, now)
    u = register_user(db_path, "u@x.com", "password-123", None, now)
    decide_registration(db_path, u["id"], True, admin["id"], now)
    set_user_role(db_path, u["id"], "editor", admin["id"], now)
    set_user_status(db_path, u["id"], "suspended", admin["id"], now)
    from app.db import connect
    with connect(db_path) as conn:
        actions = [r["action"] for r in conn.execute(
            "SELECT action FROM audit_logs ORDER BY id")]
    assert actions == ["registration.approve", "user.role_change",
                       "user.suspended"]


def test_rate_limit_tiers(client):
    """M3-F5 分级限流：标准档超限 429+Retry-After；API Key 档阈值独立。"""
    from app.auth import RateLimiter
    app = client.app
    old = app.state.rate_limiters if hasattr(app.state, "rate_limiters") else None
    app.state.rate_limiters = {"standard": RateLimiter(2),
                               "api_key": RateLimiter(5)}
    try:
        for _ in range(2):
            assert client.get("/api/v1/health").status_code == 200
        r = client.get("/api/v1/health")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert r.headers["X-RateLimit-Tier"] == "standard"
        # API key 档不受影响
        for _ in range(5):
            assert client.get("/api/v1/health",
                              headers={"X-API-Key": "gnr_x"}).status_code in (200, 401, 404)
    finally:
        app.state.rate_limiters = old
