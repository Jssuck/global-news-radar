"""第一级规则清洗（MVP 简化版）。

实现：URL 规范化 + SHA-256 精确去重、trafilatura 正文/元数据抽取、简易语种检测。
简化点（相对设计 4.1）：
- 无 newspaper4k/readability 兜底链，仅 trafilatura 主抽取；
- 无 ftfy 编码修复（trafilatura 内部已做基本解码）；
- 语种检测为字符集启发式（汉字/假名/谚文/西里尔占比）+ 源声明语种兜底，
  未引入 fastText/Lingua 双分类器；
- 去重仅第一段（URL 规范化 + SHA-256 精确去重），无 MinHash/embedding 近重段。
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import trafilatura

# 常见跟踪参数，规范化时剔除
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_cid|mc_eid|igshid|spm|ref_)", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """URL 规范化：协议/host 小写、去 fragment、去跟踪参数、query 排序、去默认端口。"""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    host = parts.hostname.lower() if parts.hostname else ""
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    path = parts.path or "/"
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not TRACKING_PARAMS.match(k)
    )
    return urlunsplit((scheme, host + port, path, urlencode(query), ""))


def url_hash(normalized_url: str) -> str:
    """规范化 URL 的 SHA-256，作为精确去重键（articles.url_hash 唯一约束）。"""
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


def extract_article(html: str, url: str) -> dict:
    """trafilatura 抽取正文与元数据；失败时返回空字段由调用方降级处理。"""
    result: dict = {"title": None, "body": None, "published_at": None, "authors": None}
    if not html:
        return result
    extracted = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        output_format="txt",
        with_metadata=True,
    )
    meta = trafilatura.extract_metadata(html, default_url=url)
    if extracted:
        result["body"] = extracted.strip() or None
    if meta:
        result["title"] = meta.title
        result["published_at"] = meta.date
        result["authors"] = meta.author
    return result


def _ratio(text: str, pattern: str) -> float:
    if not text:
        return 0.0
    return len(re.findall(pattern, text)) / max(len(text), 1)


def detect_language(text: str, fallback: str | None = None) -> str | None:
    """简易语种检测：按文字系统占比判断，识别不了则回退到源声明语种。

    仅覆盖种子源涉及的文字系统，作为 fastText/Lingua 的轻量替代（简化点）。
    """
    sample = (text or "")[:2000]
    if _ratio(sample, r"[ぁ-ゟ゠-ヿ]") > 0.05:        # 平/片假名 → 日语
        return "ja"
    if _ratio(sample, r"[가-힯]") > 0.1:               # 谚文 → 韩语
        return "ko"
    if _ratio(sample, r"[一-鿿]") > 0.1:              # 汉字（无假名）→ 中文
        return "zh"
    if _ratio(sample, r"[Ѐ-ӿ]") > 0.1:              # 西里尔 → 俄语
        return "ru"
    if _ratio(sample, r"[؀-ۿ]") > 0.1:              # 阿拉伯字母
        return "ar"
    return fallback
