"""SQLite 存储层：sources / articles / fetch_log 三张表。

合规说明：原始 HTML 不落库，只保存清洗后的正文与元数据（设计文档 1.3 非目标：
不分发全文、不存全文 HTML 冗余副本）。正文仅用于本地处理，对外输出为
「标题 + 极短摘要 + 原文链接」。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,      -- sources/*.yaml 文件名（去后缀）
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    country TEXT NOT NULL,
    language TEXT NOT NULL,
    media_type TEXT,
    influence_tier TEXT,
    feed_url TEXT NOT NULL UNIQUE,      -- rss 策略为 feed 地址；sitemap 策略为 sitemap/站点根地址
    interval_minutes INTEGER NOT NULL DEFAULT 10,  -- 源级轮询间隔（分钟，自适应调整）
    empty_streak INTEGER NOT NULL DEFAULT 0,       -- 连续无新文章轮数（自适应退避状态）
    discovery_strategy TEXT NOT NULL DEFAULT 'rss', -- rss | sitemap（M1 二级发现）
    sitemap_url TEXT,                   -- sitemap 策略的显式 sitemap 地址（可空，空则走 robots.txt 发现）
    geo_status TEXT NOT NULL DEFAULT 'unknown',    -- unknown | ok | geo_restricted
    required_region TEXT,
    geo_evidence TEXT,
    etag TEXT,
    last_modified TEXT,
    last_fetched_at TEXT,
    last_status INTEGER,
    active INTEGER NOT NULL DEFAULT 1
);

-- articles.degraded=1 表示聚合层降级记录（仅标题+URL 元数据，无正文，见设计 3.3.2 受限降级模式）

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    url TEXT NOT NULL,                    -- 规范化后的 URL
    url_hash TEXT NOT NULL UNIQUE,        -- SHA-256 精确去重约束
    title TEXT NOT NULL,
    summary TEXT,                         -- 前 200 字符摘要（对外输出）
    body TEXT,                            -- 清洗后正文（仅本地使用，不经 API 全文分发）
    language TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    degraded INTEGER NOT NULL DEFAULT 0,  -- 1=聚合层降级记录（无正文）
    event_id INTEGER REFERENCES events(id),  -- 事件簇归属（M2 第三级整理写入）
    gate1_failed INTEGER NOT NULL DEFAULT 0, -- 质量门1判负（M2-N2 判负率口径）
    cleaned_by TEXT                          -- trafilatura-* | newspaper4k | llm | NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    fetched_at TEXT NOT NULL,
    http_status INTEGER,
    result TEXT NOT NULL,                 -- ok | not_modified | geo_restricted | anti_bot | robots_blocked | error
    new_articles INTEGER NOT NULL DEFAULT 0,
    extraction_ok INTEGER NOT NULL DEFAULT 0,    -- 本轮正文抽取成功篇数
    extraction_total INTEGER NOT NULL DEFAULT 0, -- 本轮正文抓取尝试篇数
    dedup_hits INTEGER NOT NULL DEFAULT 0,       -- 本轮命中 URL 哈希去重的条数
    proxy_key TEXT,                   -- 本轮实际使用的代理 profile_key（证据留存）
    detail TEXT
);

CREATE TABLE IF NOT EXISTS proxy_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key TEXT NOT NULL UNIQUE,     -- BYO 代理配置标识（对应 proxies.yaml profile_id）
    type TEXT NOT NULL,                   -- datacenter | residential | isp | mobile
    country TEXT NOT NULL,                -- 出口地域 ISO 3166-1 alpha-2
    endpoint TEXT NOT NULL,               -- 代理接入点（host:port / URL）
    credentials_env TEXT,                 -- 本地环境变量名，凭据不落明文（设计 3.3.3）
    provider TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proxy_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,                  -- source | country | global（源级>国家级>全局）
    source_id INTEGER REFERENCES sources(id),   -- scope=source 时必填
    country_code TEXT,                    -- scope=country 时必填
    proxy_profile_id INTEGER NOT NULL REFERENCES proxy_profiles(id),
    priority INTEGER NOT NULL DEFAULT 100,-- 同级冲突取小者
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geo_hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    required_region TEXT,                 -- 判定所需出口地域（对照确认后反推，否则取源国）
    evidence TEXT,                        -- 检测证据（状态码/信号/对照结果）
    status TEXT NOT NULL DEFAULT 'open',  -- open | resolved | ignored
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS dead_letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,                  -- 管线阶段（llm_clean / organize 等）
    article_id INTEGER REFERENCES articles(id),
    payload TEXT,                         -- 失败输入/输出快照（JSON 文本）
    errors TEXT,                          -- 校验/运行错误（JSON 文本）
    status TEXT NOT NULL DEFAULT 'open',  -- open | resolved | discarded
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,                  -- clean | organize | translate 等
    model TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,                           -- 事件级标题（第三级 LLM 整理后填充）
    summary TEXT,
    iptc_tags TEXT,                       -- JSON 数组
    heat_score REAL,
    article_count INTEGER NOT NULL DEFAULT 0,
    organized INTEGER NOT NULL DEFAULT 0, -- 0=未整理占位（LLM 不可用时降级），1=已整理
    first_seen TEXT NOT NULL,
    last_updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_embeddings (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id),
    embedding BLOB NOT NULL,              -- float32 向量（numpy buffer）
    model_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,          -- PBKDF2-HMAC-SHA256 salt$hex
    role TEXT NOT NULL DEFAULT 'user',  -- admin | editor | user | api-caller（RBAC）
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|suspended
    invite_code TEXT,
    reason TEXT,                          -- 申请理由（设计 5.3.1）
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,      -- SHA-256(session token)，不落明文
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    key_hash TEXT NOT NULL UNIQUE,        -- SHA-256(api key)，不落明文
    name TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT 'read',  -- 逗号分隔：read | write | admin
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS invite_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    max_uses INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,               -- registration.approve|reject user.role_change user.suspended...
    target_type TEXT NOT NULL,
    target_id INTEGER,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gdelt_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    checked_at TEXT NOT NULL,           -- 每日漏抓对照日期
    gdelt_hits INTEGER NOT NULL DEFAULT 0,
    local_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_fetch_log_source ON fetch_log(source_id);
CREATE INDEX IF NOT EXISTS idx_geo_hints_source ON geo_hints(source_id);
CREATE INDEX IF NOT EXISTS idx_proxy_bindings_scope ON proxy_bindings(scope);
CREATE INDEX IF NOT EXISTS idx_dead_letters_status ON dead_letters(status);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
"""

# 写操作串行化锁（单进程 MVP，避免并发写 SQLite 报 database is locked）
WRITE_LOCK = threading.Lock()


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# 旧库增量迁移：M1 新增列（新库已由 SCHEMA 覆盖，旧库用 ALTER TABLE 补齐）
_MIGRATIONS = (
    ("sources", "empty_streak", "empty_streak INTEGER NOT NULL DEFAULT 0"),
    ("sources", "discovery_strategy",
     "discovery_strategy TEXT NOT NULL DEFAULT 'rss'"),
    ("sources", "sitemap_url", "sitemap_url TEXT"),
    ("fetch_log", "extraction_ok", "extraction_ok INTEGER NOT NULL DEFAULT 0"),
    ("fetch_log", "extraction_total", "extraction_total INTEGER NOT NULL DEFAULT 0"),
    ("fetch_log", "dedup_hits", "dedup_hits INTEGER NOT NULL DEFAULT 0"),
    ("fetch_log", "proxy_key", "proxy_key TEXT"),
    ("articles", "degraded", "degraded INTEGER NOT NULL DEFAULT 0"),
    ("articles", "event_id", "event_id INTEGER REFERENCES events(id)"),
    ("articles", "gate1_failed", "gate1_failed INTEGER NOT NULL DEFAULT 0"),
    ("articles", "cleaned_by", "cleaned_by TEXT"),
    ("users", "reason", "reason TEXT"),
)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """幂等加列：PRAGMA table_info 检查缺失再 ALTER TABLE。"""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if cols and column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for table, column, ddl in _MIGRATIONS:
            _ensure_column(conn, table, column, ddl)
        # M4：旧库角色别名 viewer → user（设计四角色命名）
        conn.execute("UPDATE users SET role='user' WHERE role='viewer'")
