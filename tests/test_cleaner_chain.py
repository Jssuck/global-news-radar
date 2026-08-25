"""正文抽取兜底链测试：fast → favor_recall → newspaper4k 的顺序与跳过逻辑。"""
from app import cleaner
from app.cleaner import extract_article
from tests.conftest import ARTICLE_HTML

LONG_BODY = "正文内容 " * 60  # 远超 200 字符阈值
SHORT_BODY = "过短正文"      # < 200 字符，判定为抽取失败


def _patch_trafilatura(monkeypatch, outcomes):
    """把 _extract_trafilatura 替换为按 favor_recall 返回预置结果的假实现。"""
    calls = []

    def fake(html, url, *, favor_recall):
        calls.append(favor_recall)
        return outcomes[favor_recall]

    monkeypatch.setattr(cleaner, "_extract_trafilatura", fake)
    return calls


def test_real_chain_extracts_fixture():
    """真实兜底链对合成样本一次命中（fast 或 recall 级）。"""
    meta = extract_article(ARTICLE_HTML, "https://example.com/news/story-one")
    assert meta["extractor"] in ("trafilatura-fast", "trafilatura-recall")
    assert meta["body"] and "synthetic sample article" in meta["body"]


def test_fast_success_short_circuits_chain(monkeypatch):
    calls = _patch_trafilatura(monkeypatch, {False: LONG_BODY, True: LONG_BODY})
    monkeypatch.setattr(cleaner, "_NewspaperArticle", object())  # 假装已安装
    monkeypatch.setattr(cleaner, "_extract_newspaper",
                        lambda h, u: (_ for _ in ()).throw(AssertionError("不应到达")))
    meta = extract_article("<html></html>", "https://example.com/a")
    assert meta["extractor"] == "trafilatura-fast"
    assert meta["body"] == LONG_BODY
    assert calls == [False]  # 只跑 fast，不触发后续级别


def test_short_body_falls_back_to_recall(monkeypatch):
    """fast 结果 <200 字符判定失败，降级 favor_recall。"""
    calls = _patch_trafilatura(monkeypatch, {False: SHORT_BODY, True: LONG_BODY})
    meta = extract_article("<html></html>", "https://example.com/a")
    assert meta["extractor"] == "trafilatura-recall"
    assert calls == [False, True]


def test_both_trafilatura_fail_falls_back_to_newspaper(monkeypatch):
    _patch_trafilatura(monkeypatch, {False: None, True: SHORT_BODY})
    monkeypatch.setattr(cleaner, "_NewspaperArticle", object())
    monkeypatch.setattr(cleaner, "_extract_newspaper", lambda h, u: LONG_BODY)
    meta = extract_article("<html></html>", "https://example.com/a")
    assert meta["extractor"] == "newspaper4k"
    assert meta["body"] == LONG_BODY


def test_newspaper_not_installed_skips_third_stage(monkeypatch, caplog):
    """newspaper4k 未安装：跳过第三级，记日志，不抛异常。"""
    _patch_trafilatura(monkeypatch, {False: None, True: None})
    monkeypatch.setattr(cleaner, "_NewspaperArticle", None)
    called = []
    monkeypatch.setattr(cleaner, "_extract_newspaper",
                        lambda h, u: called.append(1))
    with caplog.at_level("INFO", logger="gnr.cleaner"):
        meta = extract_article("<html></html>", "https://example.com/a")
    assert meta["extractor"] is None
    assert meta["body"] is None
    assert not called  # 第三级被跳过
    assert any("newspaper4k 未安装" in r.message for r in caplog.records)


def test_all_stages_fail_returns_empty(monkeypatch):
    _patch_trafilatura(monkeypatch, {False: None, True: None})
    monkeypatch.setattr(cleaner, "_NewspaperArticle", object())
    monkeypatch.setattr(cleaner, "_extract_newspaper", lambda h, u: SHORT_BODY)
    meta = extract_article("<html></html>", "https://example.com/a")
    assert meta["extractor"] is None
    assert meta["body"] is None
