"""认证 / 注册审核 / API Key / RBAC / 限流（设计 5.3，M3 落地）。

- 密码：PBKDF2-HMAC-SHA256（stdlib，不引 bcrypt 依赖），salt$hex 存储；
- 双通道凭证：session cookie（gnr_session，HttpOnly）或 API Key
  （Authorization: Bearer gnr_*）；两者都只存 SHA-256 摘要，不落明文；
- 注册审核三态：pending → approved | rejected（不可逆终态，设计 5.3.1）；
  首个注册用户自动成为 approved admin（自托管引导路径）；
- RBAC：admin > editor > viewer；policy 表见 ROLE_PERMS，
  后续可平滑切换到 pycasbin（接口已按 subject/action 分离）；
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

# RBAC：role → 允许动作集合（"*" 全权）。editor 管源与内容，viewer 只读。
ROLE_PERMS: dict[str, set[str]] = {
    "admin": {"*"},
    "editor": {"read", "write", "source:manage", "hint:resolve"},
    "viewer": {"read"},
}
ROLE_ORDER = {"viewer": 0, "editor": 1, "admin": 2}


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
                  invite_code: str | None, now: str) -> dict:
    """注册：首位用户直升 admin+approved（自托管引导）；其余进 pending 待审。

    邀请码存在则校验并计次；不存在的邀请码按无码处理（进入 pending）。
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("email 格式无效")
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
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
        role = "admin" if bootstrap else "viewer"
        status = "approved" if bootstrap else "pending"
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, role, status,"
            " invite_code, created_at, decided_at) VALUES (?,?,?,?,?,?,?)",
            (email, hash_password(password), role, status,
             invite_code if code_ok else None, now,
             now if bootstrap else None),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?",
                           (cur.lastrowid,)).fetchone()
    return dict(row)


def decide_registration(db_path: str, user_id: int, approve: bool,
                        admin_id: int, now: str) -> dict:
    """注册审核三态状态机：pending → approved | rejected（终态不可再变）。"""
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
    return {"id": user_id, "status": new_status}


def set_user_role(db_path: str, user_id: int, role: str) -> None:
    if role not in ROLE_PERMS:
        raise ValueError(f"role 必须是 {sorted(ROLE_PERMS)} 之一")
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))


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
        perms = ROLE_PERMS.get(self.user["role"], set())
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
    return "ip:" + (request.client.host if request.client else "unknown")
