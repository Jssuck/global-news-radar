"""M2b LLM 二级清洗测试：schema 闸门 / 确定性检查 / 重试 / DLQ / 管线集成。"""
from __future__ import annotations

import asyncio
import json

import httpx

from tests.conftest import FEED_XML


def run(coro):
    return asyncio.run(coro)


RAW_TEXT = ("Breaking: City council approves new transit plan. " * 30
            + "The vote was 7-2 after a heated session. " * 20)


def _good_payload(**over):
    body = {
        "title": "City council approves transit plan",
        "body": ("Breaking: City council approves new transit plan. " * 5
                 + "The vote was 7-2 after a heated session. " * 4),
        "language": "en",
        "published_at": "2026-06-01T08:00:00Z",
        "authors": ["Staff Writer"],
        "is_ad_or_boilerplate": False,
        "confidence": 0.9,
    }
    body.update(over)
    return json.dumps(body)


def _provider(fn, model="mock-llm"):
    from app.llm import CallableProvider
    return CallableProvider(fn, model)


def test_gate1_fail_reasons():
    """质量门1判负口径：空正文/短正文/语种分歧/缺标题。"""
    from app.llm import gate1_fail_reasons
    assert gate1_fail_reasons({"body": None, "extractor": None},
                              None, "en", 5000) == ["extract_failed",
                                                    "metadata_low_confidence"]
    assert gate1_fail_reasons({"body": "x" * 100, "extractor": "t",
                               "title": "T"},
                              None, "en", 5000) == ["body_too_short"]
    assert gate1_fail_reasons({"body": "x" * 300, "extractor": "t",
                               "title": "T"},
                              "ja", "en", 5000) == ["language_mismatch"]
    assert gate1_fail_reasons({"body": "x" * 300, "extractor": "t",
                               "title": "T"}, "en", "en", 5000) == []


def test_deterministic_check_rejects_hallucination():
    """确定性检查：清洗后正文必须与输入高重叠；发布时间不得晚于抓取时间。"""
    from app.llm import CleanedArticle, deterministic_check
    c = CleanedArticle.model_validate(json.loads(_good_payload()))
    assert deterministic_check(c, RAW_TEXT, "2026-09-11T00:00:00") == []
    # 臆造正文 → 重叠不足
    bad = c.model_copy(update={"body": "totally invented prose " * 30})
    assert "body_not_grounded_in_source" in deterministic_check(
        bad, RAW_TEXT, "2026-09-11T00:00:00")
    # 未来发布时间
    future = c.model_copy(update={"published_at": "2027-01-01"})
    assert "published_after_fetch" in deterministic_check(
        future, RAW_TEXT, "2026-09-11T00:00:00")


def test_llm_clean_success(db_path):
    """一次调用成功：校验通过 + 确定性检查通过 + llm_calls 记账。"""
    from app.db import connect
    from app.llm import llm_clean_article

    provider = _provider(lambda msgs: _good_payload())
    cleaned = run(llm_clean_article(
        db_path, provider, raw_text=RAW_TEXT,
        meta={"title": None, "extractor": None},
        reasons=["extract_failed"], fetched_at="2026-09-11T00:00:00"))
    assert cleaned is not None and cleaned.title.startswith("City council")
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["stage"] == "clean" and row["success"] == 1


def test_llm_clean_retry_then_dlq(db_path):
    """非法 JSON → 纠错重试一次 → 仍失败进 dead_letters。"""
    from app.db import connect
    from app.llm import llm_clean_article

    calls = []

    def bad_fn(msgs):
        calls.append(msgs[-1]["content"])
        return "this is not json"

    cleaned = run(llm_clean_article(
        db_path, _provider(bad_fn), raw_text=RAW_TEXT,
        meta={}, reasons=["extract_failed"], fetched_at="2026-09-11T00:00:00",
        article_id=None))
    assert cleaned is None
    assert len(calls) == 2                      # 首试 + 纠错重试一次
    assert "未通过校验" in calls[1]              # 第二次带错误反馈
    with connect(db_path) as conn:
        dlq = conn.execute("SELECT * FROM dead_letters").fetchone()
    assert dlq["stage"] == "llm_clean" and dlq["article_id"] is None


def test_llm_clean_ungrounded_body_dlq(db_path):
    """schema 合法但正文与输入不重叠 → 确定性检查失败 → DLQ。"""
    from app.db import connect
    from app.llm import llm_clean_article

    cleaned = run(llm_clean_article(
        db_path,
        _provider(lambda m: _good_payload(body="fabricated content " * 30)),
        raw_text=RAW_TEXT, meta={}, reasons=["body_too_short"],
        fetched_at="2026-09-11T00:00:00"))
    assert cleaned is None
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0] == 1


def test_pipeline_llm_clean_integration(db_path, source, settings_obj,
                                        monkeypatch):
    """管线集成：抽取失败的候选经 mock provider 清洗后入库（cleaned_by=llm）。"""
    from app import pipeline
    from app.db import connect
    from app.pipeline import fetch_source

    # 抽取链整体失败（模拟 trafilatura 抽不出正文的页面）
    monkeypatch.setattr(
        pipeline, "extract_article",
        lambda html, url: {"title": None, "body": None, "published_at": None,
                           "authors": None, "extractor": None})
    news_text = ("Council members voted to approve the downtown transit "
                 "expansion plan after a lengthy public hearing session. ")
    thin_html = ("<html><head><title>Thin Page</title></head><body>"
                 f"<div class='ad'>{news_text * 4}</div></body></html>")
    routes = {
        "https://feeds.bbci.co.uk/robots.txt": httpx.Response(
            200, text="User-agent: *\nAllow: /\n"),
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(200, text=FEED_XML),
        "https://example.com/robots.txt": httpx.Response(
            200, text="User-agent: *\nAllow: /\n"),
        "https://example.com/news/story-one": httpx.Response(200, text=thin_html),
        "https://example.com/news/story-two": httpx.Response(200, text=thin_html),
    }

    def factory(timeout, user_agent, proxy=None):
        return httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: routes.get(str(req.url)) or httpx.Response(404)))

    def llm_fn(msgs):
        # LLM 从 raw_text 中还原出正文（须与输入高重叠才过确定性检查）
        return json.dumps({
            "title": "Thin Page", "body": news_text * 4, "language": "en",
            "published_at": None, "authors": [], "is_ad_or_boilerplate": False,
            "confidence": 0.6})

    from app.llm import CallableProvider
    result = run(fetch_source(db_path, source, settings_obj,
                              client_factory=factory,
                              llm_provider=CallableProvider(llm_fn)))
    assert result["result"] == "ok"
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT cleaned_by, gate1_failed, body FROM articles"
            " WHERE source_id=?", (source["id"],)).fetchall()
    assert rows and all(r["cleaned_by"] == "llm" and r["gate1_failed"] == 1
                        for r in rows)


def test_pipeline_llm_ad_discard(db_path, source, settings_obj, monkeypatch):
    """LLM 判定整体为广告/导航 → 丢弃分支（不入库）。"""
    from app import pipeline
    from app.db import connect
    from app.llm import CallableProvider
    from app.pipeline import fetch_source

    monkeypatch.setattr(
        pipeline, "extract_article",
        lambda html, url: {"title": None, "body": None, "published_at": None,
                           "authors": None, "extractor": None})
    ad_text = ("Subscribe now to unlock premium access and read more of "
               "our exclusive offers and newsletters every single day. ")
    ad_html = f"<html><body><p>{ad_text * 3}</p></body></html>"
    routes = {
        "https://feeds.bbci.co.uk/robots.txt": httpx.Response(
            200, text="User-agent: *\nAllow: /\n"),
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(200, text=FEED_XML),
        "https://example.com/robots.txt": httpx.Response(
            200, text="User-agent: *\nAllow: /\n"),
        "https://example.com/news/story-one": httpx.Response(200, text=ad_html),
        "https://example.com/news/story-two": httpx.Response(200, text=ad_html),
    }

    def factory(timeout, user_agent, proxy=None):
        return httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: routes.get(str(req.url)) or httpx.Response(404)))

    ad_fn = lambda m: json.dumps({
        "title": "Subscribe", "body": ad_text * 3, "language": "en",
        "published_at": None, "authors": [], "is_ad_or_boilerplate": True,
        "confidence": 0.95})

    result = run(fetch_source(db_path, source, settings_obj,
                              client_factory=factory,
                              llm_provider=CallableProvider(ad_fn)))
    assert result["new_articles"] == 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


def test_pipeline_no_llm_gate_counts(db_path, source, settings_obj):
    """provider 未配置时：判负文章照常入库，gate1_failed=1 供判负率监控。"""
    import os

    from app.db import connect
    from app.pipeline import fetch_source

    os.environ.pop("GNR_LLM_BASE_URL", None)
    thin = "<html><body><p>x</p></body></html>"
    routes = {
        "https://feeds.bbci.co.uk/robots.txt": httpx.Response(
            200, text="User-agent: *\nAllow: /\n"),
        "https://feeds.bbci.co.uk/news/rss.xml": httpx.Response(200, text=FEED_XML),
        "https://example.com/robots.txt": httpx.Response(
            200, text="User-agent: *\nAllow: /\n"),
        "https://example.com/news/story-one": httpx.Response(200, text=thin),
        "https://example.com/news/story-two": httpx.Response(200, text=thin),
    }

    def factory(timeout, user_agent, proxy=None):
        return httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: routes.get(str(req.url)) or httpx.Response(404)))

    run(fetch_source(db_path, source, settings_obj, client_factory=factory,
                     llm_provider=None))
    with connect(db_path) as conn:
        rows = conn.execute("SELECT gate1_failed FROM articles").fetchall()
    assert rows and all(r["gate1_failed"] == 1 for r in rows)
