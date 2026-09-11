"""BYO 代理：profiles/bindings 存储与三级绑定解析（设计 3.3.3，M2 落地）。

- 项目只定义配置接口，不分发、不内置任何代理资源；凭据仅存本地环境变量引用
  （credentials_env），不落明文、不返回 API。
- 绑定解析优先级：源级 > 国家级 > 全局；同级取 priority 最小者。
- 启动时把 config/proxies.yaml 导入 DB（upsert），运行期以 DB 为准；
  API（/api/v1/proxy-profiles|proxy-bindings）直接读写 DB。
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import BASE_DIR
from .db import WRITE_LOCK, connect

PROXY_TYPES = ("datacenter", "residential", "isp", "mobile")
BINDING_SCOPES = ("source", "country", "global")


def load_proxy_config(config_path: str) -> dict:
    """加载 YAML 代理配置文件；文件不存在时回退到示例（仅展示用）。"""
    import yaml

    path = Path(config_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    fallback = False
    if not path.is_file():
        path = BASE_DIR / "config" / "proxies.example.yaml"
        fallback = True
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"profiles": [], "bindings": [], "is_example": True,
                "error": "配置文件解析失败"}
    return {
        "profiles": doc.get("profiles") or [],
        "bindings": doc.get("bindings") or [],
        "is_example": fallback,
        "path": str(path),
    }


def sync_proxy_config(db_path: str, config_path: str, now: str) -> int:
    """把 YAML 代理配置 upsert 进 DB（profiles 按 profile_id、bindings 按全字段判重）。

    返回导入的 profile 数；示例文件（is_example）不导入——它只是格式演示。
    """
    cfg = load_proxy_config(config_path)
    if cfg.get("is_example"):
        return 0
    imported = 0
    with WRITE_LOCK, connect(db_path) as conn:
        for p in cfg["profiles"]:
            if not p.get("profile_id") or not p.get("endpoint"):
                continue
            conn.execute(
                """
                INSERT INTO proxy_profiles (profile_key, type, country, endpoint,
                                            credentials_env, provider, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                    type=excluded.type, country=excluded.country,
                    endpoint=excluded.endpoint,
                    credentials_env=excluded.credentials_env,
                    provider=excluded.provider, notes=excluded.notes
                """,
                (p["profile_id"], p.get("type", "datacenter"),
                 p.get("country", ""), p["endpoint"], p.get("credentials_env"),
                 p.get("provider"), p.get("notes"), now),
            )
            imported += 1
        for b in cfg["bindings"]:
            scope = b.get("scope")
            if scope not in BINDING_SCOPES:
                continue
            profile = conn.execute(
                "SELECT id FROM proxy_profiles WHERE profile_key = ?",
                (b.get("profile_id"),)).fetchone()
            if not profile:
                continue
            source_id = None
            if scope == "source":
                row = conn.execute(
                    "SELECT id FROM sources WHERE source_key = ?",
                    (b.get("source"),)).fetchone()
                source_id = row["id"] if row else None
                if source_id is None:
                    continue
            country_code = b.get("country") if scope == "country" else None
            dup = conn.execute(
                """
                SELECT 1 FROM proxy_bindings
                WHERE scope = ? AND source_id IS ? AND country_code IS ?
                  AND proxy_profile_id = ?
                """,
                (scope, source_id, country_code, profile["id"]),
            ).fetchone()
            if not dup:
                conn.execute(
                    """
                    INSERT INTO proxy_bindings
                        (scope, source_id, country_code, proxy_profile_id,
                         priority, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (scope, source_id, country_code, profile["id"],
                     b.get("priority", 100), now),
                )
    return imported


def resolve_proxy(db_path: str, source: dict) -> dict | None:
    """三级绑定解析：源级 > 国家级 > 全局，同级取 priority 最小者。

    返回 profile dict（含凭据 env 引用名，不含凭据值）；无绑定返回 None。
    """
    with connect(db_path) as conn:
        for scope, key, val in (("source", "source_id", source.get("id")),
                                ("country", "country_code", source.get("country"))):
            if val is None:
                continue
            row = conn.execute(
                f"""
                SELECT p.* FROM proxy_bindings b
                JOIN proxy_profiles p ON p.id = b.proxy_profile_id
                WHERE b.scope = ? AND b.{key} = ?
                ORDER BY b.priority ASC LIMIT 1
                """,
                (scope, val),
            ).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            """
            SELECT p.* FROM proxy_bindings b
            JOIN proxy_profiles p ON p.id = b.proxy_profile_id
            WHERE b.scope = 'global'
            ORDER BY b.priority ASC LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def proxy_url(profile: dict) -> str | None:
    """把 profile 转为 httpx 可用的代理 URL（含凭据注入，凭据取自环境变量）。

    endpoint 支持 host:port 或完整 URL；credentials_env 指向形如 user:pass 的
    环境变量，注入为 URL 的 userinfo 段。无凭据引用时原样返回。
    """
    endpoint = (profile.get("endpoint") or "").strip()
    if not endpoint:
        return None
    if "://" not in endpoint:
        endpoint = "http://" + endpoint
    cred = os.environ.get(profile.get("credentials_env") or "", "")
    if not cred:
        return endpoint
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, f"{cred}@{host}", parts.path or "/",
                      "", ""))


def alt_country_profile(db_path: str, exclude_country: str | None) -> dict | None:
    """取一个与 exclude_country 不同的代理 profile（geo 中信号对照验证用）。

    优先数据中心类型（设计 3.3.3：住宅代理只在证实受限后按需启用）。
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM proxy_profiles WHERE country != ?"
            " ORDER BY CASE type WHEN 'datacenter' THEN 0 ELSE 1 END, id",
            (exclude_country or "",),
        ).fetchall()
    return dict(rows[0]) if rows else None


def create_profile(db_path: str, data: dict, now: str) -> dict:
    """创建代理配置（API 层调用）；返回落库后的 profile dict。"""
    if data.get("type") not in PROXY_TYPES:
        raise ValueError(f"type 必须是 {PROXY_TYPES} 之一")
    if not data.get("endpoint"):
        raise ValueError("endpoint 必填")
    with WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO proxy_profiles (profile_key, type, country, endpoint,
                                        credentials_env, provider, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (data["profile_key"], data["type"], data.get("country", ""),
             data["endpoint"], data.get("credentials_env"),
             data.get("provider"), data.get("notes"), now),
        )
        row = conn.execute("SELECT * FROM proxy_profiles WHERE id = ?",
                           (cur.lastrowid,)).fetchone()
    return dict(row)


def create_binding(db_path: str, data: dict, now: str) -> dict:
    """创建三级绑定（API 层调用）；校验 scope 必填字段与 profile 存在性。"""
    scope = data.get("scope")
    if scope not in BINDING_SCOPES:
        raise ValueError(f"scope 必须是 {BINDING_SCOPES} 之一")
    with WRITE_LOCK, connect(db_path) as conn:
        profile = conn.execute(
            "SELECT * FROM proxy_profiles WHERE id = ?",
            (data.get("proxy_profile_id"),)).fetchone()
        if not profile:
            raise ValueError("proxy_profile_id 不存在")
        source_id = None
        if scope == "source":
            src = conn.execute("SELECT id FROM sources WHERE id = ?",
                               (data.get("source_id"),)).fetchone()
            if not src:
                raise ValueError("scope=source 时 source_id 必填且须存在")
            source_id = src["id"]
        country_code = None
        if scope == "country":
            country_code = (data.get("country_code") or "").lower()
            if len(country_code) != 2:
                raise ValueError("scope=country 时 country_code 必填（ISO alpha-2）")
        cur = conn.execute(
            """
            INSERT INTO proxy_bindings
                (scope, source_id, country_code, proxy_profile_id, priority, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (scope, source_id, country_code, profile["id"],
             data.get("priority", 100), now),
        )
        row = conn.execute("SELECT * FROM proxy_bindings WHERE id = ?",
                           (cur.lastrowid,)).fetchone()
    return dict(row)
