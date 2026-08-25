"""地域受限判定逻辑测试（mock 响应，离线）。

判定规则与 skills/geo-block-triage/scripts/geo_verdict.py 保持一致。
"""
from app.geo import evaluate_response, geo_hint, region_name


def test_http_451_is_geo_restricted():
    v = evaluate_response(451, "")
    assert v.verdict == "geo_restricted"
    assert "http_451" in v.signals


def test_403_with_geo_phrase_is_restricted():
    body = "Sorry, this content is not available in your region."
    v = evaluate_response(403, body)
    assert v.verdict == "geo_restricted"
    assert "http_403_geo_phrase" in v.signals


def test_403_without_geo_phrase_is_not_restricted():
    v = evaluate_response(403, "Access denied: bad credentials")
    assert v.verdict == "ok"


def test_429_and_5xx_excluded_as_anti_bot():
    for status in (429, 500, 502, 503):
        assert evaluate_response(status, "").verdict == "anti_bot"


def test_challenge_page_excluded():
    v = evaluate_response(403, "Please complete the captcha challenge to continue")
    assert v.verdict == "anti_bot"


def test_normal_200_is_ok():
    assert evaluate_response(200, "<html>news</html>").verdict == "ok"


def test_hint_text_mentions_region_and_proxy():
    hint = geo_hint("us", "HTTP 451")
    assert "美国" in hint and "代理" in hint and "451" in hint
    assert region_name("jp") == "日本"
    assert region_name("xx") == "XX"  # 未收录国家码回退大写
