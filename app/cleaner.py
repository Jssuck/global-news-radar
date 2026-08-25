"""第一级规则清洗（M1 版）。

实现：URL 规范化 + SHA-256 精确去重、正文抽取兜底链、简易语种检测。
抽取兜底链（设计 4.1，M1 落地）：
    trafilatura(fast 模式) → trafilatura(favor_recall 模式) → newspaper4k（可选）
判定失败标准：正文为空或 < 200 字符（沿 MVP 口径）；逐级失败即降级并记日志，
newspaper4k 为可选依赖（optional import），未安装则跳过该级。
简化点（相对设计 4.1）：
- 无 ftfy 编码修复（trafilatura 内部已做基本解码）；
- 语种检测为字符集启发式（汉字/假名/谚文/西里尔占比）+ 源声明语种兜底，
  未引入 fastText/Lingua 双分类器；
- 去重仅第一段（URL 规范化 + SHA-256 精确去重），无 MinHash/embedding 近重段。
"""
from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import trafilatura

log = logging.getLogger("gnr.cleaner")

# newspaper4k 为可选依赖（不入 requirements.txt 主依赖），未安装则第三级兜底跳过
try:  # pragma: no cover - 取决于环境是否安装
    from newspaper import Article as _NewspaperArticle
except ImportError:  # pragma: no cover
    _NewspaperArticle = None

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


# 正文抽取失败判定阈值（沿 MVP 口径：正文空或短于 200 字符视为失败）
MIN_BODY_LEN = 200


def _body_ok(body: str | None) -> bool:
    """抽取结果是否达标：非空且长度 ≥ MIN_BODY_LEN。"""
    return bool(body) and len(body) >= MIN_BODY_LEN


def _extract_trafilatura(html: str, url: str, *, favor_recall: bool) -> str | None:
    """trafilatura 抽取正文（fast 或 favor_recall 模式），失败返回 None。"""
    extracted = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        output_format="txt",
        favor_recall=favor_recall,
        fast=not favor_recall,
    )
    return extracted.strip() if extracted else None


def _extract_newspaper(html: str, url: str) -> str | None:
    """newspaper4k 第三级兜底；未安装返回 None（由调用方记日志）。"""
    if _NewspaperArticle is None:
        return None
    try:  # newspaper 解析异常时按失败处理，不中断兜底链
        article = _NewspaperArticle(url)
        article.set_html(html)
        article.parse()
        text = (article.text or "").strip()
        return text or None
    except Exception:
        log.debug("newspaper4k 抽取异常", exc_info=True)
        return None


def extract_article(html: str, url: str) -> dict:
    """三级兜底链抽取正文与元数据；全部失败返回空字段由调用方降级处理。

    返回 dict 含 extractor 字段记录命中级别：
    trafilatura-fast / trafilatura-recall / newspaper4k / None。
    """
    result: dict = {"title": None, "body": None, "published_at": None,
                    "authors": None, "extractor": None}
    if not html:
        return result

    # 元数据始终走 trafilatura（即使正文兜底到 newspaper4k，标题/日期仍可用）
    meta = trafilatura.extract_metadata(html, default_url=url)
    if meta:
        result["title"] = meta.title
        result["published_at"] = meta.date
        result["authors"] = meta.author

    # 第一级：trafilatura fast 模式（最快路径）
    body = _extract_trafilatura(html, url, favor_recall=False)
    if _body_ok(body):
        result.update(body=body, extractor="trafilatura-fast")
        return result

    # 第二级：trafilatura favor_recall 模式（牺牲精度换召回）
    body = _extract_trafilatura(html, url, favor_recall=True)
    if _body_ok(body):
        log.info("fast 模式抽取不达标，favor_recall 兜底成功: %s", url)
        result.update(body=body, extractor="trafilatura-recall")
        return result

    # 第三级：newspaper4k（可选依赖，未安装则跳过并记日志）
    if _NewspaperArticle is None:
        log.info("正文抽取前两级不达标，newspaper4k 未安装，跳过第三级: %s", url)
        return result
    body = _extract_newspaper(html, url)
    if _body_ok(body):
        log.info("trafilatura 两级均不达标，newspaper4k 兜底成功: %s", url)
        result.update(body=body, extractor="newspaper4k")
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
