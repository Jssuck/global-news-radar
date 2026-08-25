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
    feed_url TEXT NOT NULL UNIQUE,
    interval_minutes INTEGER NOT NULL DEFAULT 10,  -- 源级轮询间隔（分钟）
    geo_status TEXT NOT NULL DEFAULT 'unknown',    -- unknown | ok | geo_restricted
    required_region TEXT,
    geo_evidence TEXT,
    etag TEXT,
    last_modified TEXT,
    last_fetched_at TEXT,
    last_status INTEGER,
    active INTEGER NOT NULL DEFAULT 1
);

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
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    fetched_at TEXT NOT NULL,
    http_status INTEGER,
    result TEXT NOT NULL,                 -- ok | not_modified | geo_restricted | anti_bot | error
    new_articles INTEGER NOT NULL DEFAULT 0,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_fetch_log_source ON fetch_log(source_id);
"""

# 写操作串行化锁（单进程 MVP，避免并发写 SQLite 报 database is locked）
WRITE_LOCK = threading.Lock()


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
