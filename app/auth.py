"""认证 / 注册审核 / API Key / RBAC / 限流（设计 5.3，M3 落地）。

- 密码：PBKDF2-HMAC-SHA256（stdlib，不引 bcrypt 依赖），salt$hex 存储；
- 双通道凭证：session cookie（gnr_session，HttpOnly）或 API Key
  （Authorization: Bearer gnr_*）；两者都只存 SHA-256 摘要，不落明文；
- 注册状态机（设计 5.3.1，六条迁移路径）：实例开关三档
  open / approval / invite；[*]→approved(开放注册) 、[*]→pending(审批)、
  邀请码在任何模式下旁路直通 approved；pending→approved|rejected（终态）、
  approved→suspended（封禁，session+API key 即时吊销）、suspended→approved；
  首个注册用户自动成为 approved admin（自托管引导路径）；
- RBAC 四角色：admin / editor / user / api-caller（仅 API Key、无 Web 会话）；
  policy 表见 ROLE_PERMS，接口按 subject/action 分离可平移 pycasbin；
  角色变更、注册决策、封禁解封均落 audit_logs；
- 限流：内存令牌桶，按 api_key 或 IP 计（MVP 单进程口径，
  分布式部署时换成 Valkey 实现）。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .db import WRITE_LOCK, connect

SESSION_COOKIE = "gnr_session"
SESSION_TTL = 7 * 24 * 3600          # 7 天
PBKDF2_ROUNDS = 120_000

# RBAC：role → 允许动作集合（"*" 全权）。editor 管源与内容，user 只读，
# api-caller 仅 API Key 通道（无 Web session，scopes 进一步收敛）。
ROLE_PERMS: dict[str, set[str]] = {
    "admin": {"*"},
    "editor": {"read", "write", "source:manage", "hint:resolve"},
    "user": {"read"},
    "api-caller": {"read"},
}
ROLE_ORDER = {"api-caller": 0, "user": 1, "editor": 2, "admin": 3}
# 历史别名：旧库 viewer → user
_ROLE_ALIAS = {"viewer": "user"}

# 限流两档（设计 5.3 / M3-F5）：匿名与 session 用户走标准档，
# API Key 调用方走高频档。
RATE_TIER_STANDARD = "standard"     # 60–100 req/min（默认 100）
RATE_TIER_API_KEY = "api_key"       # 500–1000 req/min（默认 600）


# ---- 密码 / 凭证 ----


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    cand = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return hmac.compare_digest(cand, digest)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- 用户与注册审核 ----


def register_user(db_path: str, email: str, password: str,
                  invite_code: str | None, now: str,
                  *, registration_mode: str = "approval",
                  reason: str | None = None) -> dict:
    """注册（设计 5.3.1 三档开关 + 邀请码旁路）。

    registration_mode：open 直接 approved；approval 进 pending；
    invite 必须持有效邀请码（任何模式下邀请码皆旁路直通 approved）。
    首位用户直升 admin+approved（自托管引导）。
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("email 格式无效")
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    if registration_mode not in ("open", "approval", "invite"):
        raise ValueError("registration_mode 必须是 open|approval|invite")
    with WRITE_LOCK, connect(db_path) as conn:
        n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        dup = conn.execute("SELECT 1 FROM users WHERE email=?",
                           (email,)).fetchone()
        if dup:
            raise ValueError("该邮箱已注册")
        bootstrap = n_users == 0
        code_ok = False
        if invite_code:
            inv = conn.execute(
                "SELECT * FROM invite_codes WHERE code=?", (invite_code,)
            ).fetchone()
            usable = (inv and inv["used_count"] < inv["max_uses"]
                      and (not inv["expires_at"] or inv["expires_at"] > now))
            if usable:
                conn.execute(
                    "UPDATE invite_codes SET used_count=used_count+1"
                    " WHERE id=?", (inv["id"],))
                code_ok = True
            elif registration_mode == "invite":
                raise ValueError("邀请码无效、已用尽或已过期")
        elif registration_mode == "invite":
            raise ValueError("当前为邀请制注册，请提供有效邀请码")
        role = "admin" if bootstrap else "user"
        status = ("approved" if bootstrap or code_ok
                  or registration_mode == "open" else "pending")
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, role, status,"
            " invite_code, reason, created_at, decided_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (email, hash_password(password), role, status,
             invite_code if code_ok else None, reason, now,
             now if status == "approved" else None),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?",
                           (cur.lastrowid,)).fetchone()
    return dict(row)


def _audit(conn, actor_id: int | None, action: str, target_type: str,
           target_id: int | None, detail: str, now: str) -> None:
    """审计日志：角色变更/注册决策/封禁解封等敏感操作留痕（设计 5.3）。"""
    conn.execute(
        "INSERT INTO audit_logs (actor_id, action, target_type, target_id,"
        " detail, created_at) VALUES (?,?,?,?,?,?)",
        (actor_id, action, target_type, target_id, detail, now))


def decide_registration(db_path: str, user_id: int, approve: bool,
                        admin_id: int, now: str) -> dict:
    """注册审核：pending → approved | rejected（终态不可再变）。"""
    with WRITE_LOCK, connect(db_path) as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?",
                            (user_id,)).fetchone()
        if not user:
            raise ValueError("用户不存在")
        if user["status"] != "pending":
            raise ValueError(f"仅 pending 状态可审核（当前 {user['status']}）")
        new_status = "approved" if approve else "rejected"
        conn.execute(
            "UPDATE users SET status=?, decided_at=?, decided_by=? WHERE id=?",
            (new_status, now, admin_id, user_id))
        _audit(conn, admin_id,
               "registration.approve" if approve else "registration.reject",
               "user", user_id, user["email"], now)
    return {"id": user_id, "status": new_status}


def set_user_status(db_path: str, user_id: int, status: str,
                    admin_id: int, now: str) -> dict:
    """approved→suspended（封禁，session+API key 即时吊销）、suspended→approved。

    suspended 是终态外唯一允许回流的管控状态；pending/rejected 不接收此调用。
    """
    if status not in ("approved", "suspended"):
        raise ValueError("status 必须是 approved|suspended")
    with WRITE_LOCK, connect(db_path) as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?",
                            (user_id,)).fetchone()
        if not user:
            raise ValueError("用户不存在")
        allowed = {"approved": {"suspended"}, "suspended": {"approved"}}
        if status not in allowed.get(user["status"], set()):
            raise ValueError(
                f"非法迁移 {user['status']}→{status}（仅允许 "
                "approved↔suspended）")
        conn.execute("UPDATE users SET status=? WHERE id=?",
                     (status, user_id))
        if status == "suspended":
            # session 即时吊销；API key 吊销（resolve 时 status!=approved 也会拦）
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.execute(
                "UPDATE api_keys SET revoked_at=? WHERE user_id=?"
                " AND revoked_at IS NULL", (now, user_id))
        _audit(conn, admin_id,
               f"user.{status}", "user", user_id, user["email"], now)
    return {"id": user_id, "status": status}


def set_user_role(db_path: str, user_id: int, role: str,
                  admin_id: int, now: str) -> None:
    role = _ROLE_ALIAS.get(role, role)
    if role not in ROLE_PERMS:
        raise ValueError(f"role 必须是 {sorted(ROLE_PERMS)} 之一")
    with WRITE_LOCK, connect(db_path) as conn:
        old = conn.execute("SELECT role FROM users WHERE id=?",
                           (user_id,)).fetchone()
        if not old:
            raise ValueError("用户不存在")
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        _audit(conn, admin_id, "user.role_change", "user", user_id,
               f"{old['role']}→{role}", now)


def create_invite(db_path: str, code: str, max_uses: int,
                  admin_id: int, now: str,
                  expires_at: str | None = None) -> dict:
    with WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO invite_codes (code, max_uses, created_by, created_at,"
            " expires_at) VALUES (?,?,?,?,?)",
            (code, max_uses, admin_id, now, expires_at))
        return dict(conn.execute("SELECT * FROM invite_codes WHERE id=?",
                                 (cur.lastrowid,)).fetchone())


# ---- session / API key ----


def login_user(db_path: str, email: str, password: str,
               now: str) -> tuple[str, dict] | None:
    """校验密码+approved 状态 → 签发 session token；失败返回 None。"""
    with connect(db_path) as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?",
                            (email.strip().lower(),)).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            return None
        if user["status"] != "approved":
            return None
        if _ROLE_ALIAS.get(user["role"], user["role"]) == "api-caller":
            return None    # api-caller 角色仅 API Key 通道，无 Web 会话
        token = "gnr_" + secrets.token_urlsafe(32)
        expires = _ts(now, SESSION_TTL)
        with WRITE_LOCK:
            conn.execute(
                "INSERT INTO sessions (user_id, token_hash, created_at,"
                " expires_at) VALUES (?,?,?,?)",
                (user["id"], _hash_token(token), now, expires))
    return token, dict(user)


def logout(db_path: str, token: str) -> None:
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?",
                     (_hash_token(token),))


def _ts(now_iso: str, add_seconds: int) -> str:
    from datetime import UTC, datetime, timedelta
    dt = datetime.fromisoformat(now_iso)
    return (dt + timedelta(seconds=add_seconds)).astimezone(UTC).isoformat(
        timespec="seconds")


def resolve_principal(db_path: str, request: Request) -> dict | None:
    """从请求解析主体：session cookie 或 API Key；返回 user dict 或 None。"""
    now = time.time()
    token = request.cookies.get(SESSION_COOKIE)
    with connect(db_path) as conn:
        if token:
            row = conn.execute(
                """
                SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (_hash_token(token),),
            ).fetchone()
            if row:
                sess = conn.execute(
                    "SELECT expires_at FROM sessions WHERE token_hash=?",
                    (_hash_token(token),)).fetchone()
                from datetime import datetime
                if datetime.fromisoformat(sess["expires_at"]).timestamp() > now:
                    return dict(row)
        auth = request.headers.get("Authorization", "")
        api_key = request.headers.get("X-API-Key") or (
            auth[7:] if auth.startswith("Bearer ") else None)
        if api_key:
            row = conn.execute(
                """
                SELECT u.*, k.scopes FROM api_keys k
                JOIN users u ON u.id = k.user_id
                WHERE k.key_hash = ? AND k.revoked_at IS NULL
                """,
                (_hash_token(api_key),),
            ).fetchone()
            if row and row["status"] == "approved":
                d = dict(row)
                d["_scopes"] = (row["scopes"] or "read").split(",")
                return d
    return None


def create_api_key(db_path: str, user_id: int, name: str,
                   scopes: str, now: str) -> str:
    """签发 API Key：明文只返回一次，库中仅存 SHA-256 摘要。"""
    key = "gnr_" + secrets.token_urlsafe(32)
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            "INSERT INTO api_keys (user_id, key_hash, name, scopes, created_at)"
            " VALUES (?,?,?,?,?)",
            (user_id, _hash_token(key), name, scopes or "read", now))
    return key


def revoke_api_key(db_path: str, key_id: int, user_id: int,
                   now: str) -> bool:
    with WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE api_keys SET revoked_at=? WHERE id=? AND user_id=?",
            (now, key_id, user_id))
    return cur.rowcount > 0


# ---- FastAPI 依赖：认证 + RBAC ----


@dataclass
class Principal:
    user: dict
    via: str                # session | api_key
    scopes: list[str]

    def can(self, action: str) -> bool:
        role = _ROLE_ALIAS.get(self.user["role"], self.user["role"])
        perms = ROLE_PERMS.get(role, set())
        if "*" in perms or action in perms:
            return True
        if self.via == "api_key":
            return action in self.scopes or "admin" in self.scopes
        return False


def current_principal(request: Request) -> Principal | None:
    user = resolve_principal(request.app.state.settings.db_path, request)
    if not user:
        return None
    via = "api_key" if "_scopes" in user else "session"
    return Principal(user=user, via=via,
                     scopes=user.get("_scopes", ["read"]))


def require_user(request: Request) -> Principal:
    p = current_principal(request)
    if not p:
        raise HTTPException(status_code=401, detail="未认证")
    return p


def require_perm(action: str):
    """RBAC 依赖工厂：require_perm('source:manage') 等。"""
    def dep(request: Request) -> Principal:
        p = require_user(request)
        if not p.can(action):
            raise HTTPException(status_code=403, detail="权限不足")
        return p
    return dep


def require_admin(request: Request) -> Principal:
    p = require_user(request)
    if p.user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return p


# ---- 内存限流（令牌桶，MVP 单进程口径） ----


class RateLimiter:
    """按 key（API key 摘要或 IP）的令牌桶限流。"""

    def __init__(self, rate_per_min: int = 60):
        self.rate = rate_per_min
        self._buckets: dict[str, tuple[float, float]] = {}  # key → (tokens, ts)

    def allow(self, key: str) -> bool:
        now = time.time()
        tokens, ts = self._buckets.get(key, (self.rate, now))
        tokens = min(self.rate, tokens + (now - ts) * self.rate / 60)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        return True


def rate_limit_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    api_key = request.headers.get("X-API-Key") or (
        auth[7:] if auth.startswith("Bearer ") else None)
    if api_key:
        return "key:" + _hash_token(api_key)[:16]
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return "sess:" + _hash_token(token)[:16]
    return "ip:" + (request.client.host if request.client else "unknown")


def rate_tier(request: Request) -> str:
    """限流分层（设计 M3-F5）：API Key 调用方走 api_key 档，其余走标准档。"""
    auth = request.headers.get("Authorization", "")
    if request.headers.get("X-API-Key") or auth.startswith("Bearer "):
        return RATE_TIER_API_KEY
    return RATE_TIER_STANDARD
