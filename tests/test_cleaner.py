"""URL 规范化、SHA-256 去重、正文抽取与语种检测的单元测试。"""
import sqlite3

import pytest

from app.cleaner import detect_language, extract_article, normalize_url, url_hash
from app.db import connect
from tests.conftest import ARTICLE_HTML


def test_normalize_url_strips_tracking_and_fragment():
    url = "HTTPS://Example.COM:443/news/story?utm_source=feed&id=42&utm_campaign=x#sec"
    assert normalize_url(url) == "https://example.com/news/story?id=42"


def test_normalize_url_sorts_query_and_keeps_meaningful_params():
    a = normalize_url("https://example.com/a?b=2&a=1")
    b = normalize_url("https://example.com/a?a=1&b=2")
    assert a == b == "https://example.com/a?a=1&b=2"


def test_url_hash_stable_and_distinct():
    h1 = url_hash(normalize_url("https://example.com/x?utm_medium=social"))
    h2 = url_hash(normalize_url("https://example.com/x"))
    assert h1 == h2  # 跟踪参数不影响去重键
    assert url_hash("https://example.com/y") != h1
    assert len(h1) == 64


def test_url_hash_unique_constraint(db_path):
    """articles.url_hash 唯一约束：同 hash 第二次插入必须失败。"""
    digest = url_hash("https://example.com/dup")
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO articles (source_id, url, url_hash, title, fetched_at)"
            " VALUES (1, ?, ?, 't', '2026-06-01')",
            ("https://example.com/dup", digest))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO articles (source_id, url, url_hash, title, fetched_at)"
                " VALUES (1, ?, ?, 't2', '2026-06-01')",
                ("https://example.com/dup", digest))


def test_extract_article_from_synthetic_html():
    meta = extract_article(ARTICLE_HTML, "https://example.com/news/story-one")
    assert meta["title"] == "Sample Story One"
    assert meta["body"] and "synthetic sample article" in meta["body"]
    assert meta["published_at"] is not None


def test_detect_language_scripts():
    assert detect_language("これはテストのニュース記事です。日本語の本文。") == "ja"
    assert detect_language("这是一段用于测试的中文新闻正文内容。") == "zh"
    assert detect_language("이것은 테스트용 한국어 뉴스 기사입니다.") == "ko"
    # 拉丁字母无法识别时回退源声明语种
    assert detect_language("plain english text", fallback="en") == "en"
    assert detect_language("", fallback="fr") == "fr"
