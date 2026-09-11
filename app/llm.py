"""第二级 LLM 清洗（设计 4.2，M2 落地）。

定位：只处理第一级规则清洗质量门判负的子集（空正文/过短/抽取链失败/
语种分歧/元数据低置信），是全管线成本的第一道阀门。

- Provider 抽象：OpenAI 兼容接口（LiteLLM 网关语义：base_url + model），
  默认本地开源模型/商用 API 可插拔；GNR_LLM_BASE_URL 未配置时管线跳过本级。
- 输出闸门：CleanedArticle JSON Schema → Pydantic 校验 → 确定性检查
  （schema 是闸门而非正确性保证：清洗后正文必须与输入文本高重叠、
  发布时间不得晚于抓取时间）。
- 失败语义：JSON 解析/校验失败 → 纠错 prompt 重试 ≤1 次 → 仍失败进
  dead_letters 死信队列（不静默丢失）。
- 成本记录：每次调用写 llm_calls（stage/model/tokens/success）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from .db import WRITE_LOCK, connect

log = logging.getLogger("gnr.llm")

# ---- 输出 Schema（设计 4.2.1 CleanedArticle） ----


class CleanedArticle(BaseModel):
    """LLM 清洗输出的强制结构（schema 闸门）。"""
    title: str = Field(min_length=1)
    body: str = Field(min_length=100,
                      description="仅新闻正文，不含广告、推荐链接、订阅引导")
    language: str = Field(pattern=r"^[a-z]{2,3}(-[A-Z][a-z]{3})?$")
    published_at: str | None = None
    authors: list[str] = Field(default_factory=list)
    is_ad_or_boilerplate: bool = Field(
        default=False, description="整体判定为非新闻内容时为 true，触发丢弃分支")
    confidence: float = Field(ge=0.0, le=1.0)


SYSTEM_PROMPT = (
    "你是新闻正文清洗器。输入是网页抓取的一次尝试结果（可能为空、截断或混入"
    "广告/导航/订阅引导等噪声）。任务：只输出一个 JSON 对象，字段："
    "title, body, language(ISO 639), published_at(ISO8601 或 null), "
    "authors(数组), is_ad_or_boilerplate(整体不是新闻时为 true), "
    "confidence(0-1)。body 必须只含新闻正文，必须来自输入文本，不得臆造、"
    "不得翻译、不得续写。无法还原正文时 is_ad_or_boilerplate=true。")


# ---- Provider 抽象（LiteLLM 语义：base_url + model 名） ----


class ChatProvider(Protocol):
    """OpenAI 兼容 chat 接口的最小协议；实现须返回 (text, usage_dict)。"""
    model: str

    async def chat(self, messages: list[dict], *, max_tokens: int,
                   json_mode: bool = True) -> tuple[str, dict]: ...


class OpenAICompatibleProvider:
    """任意 OpenAI 兼容端点（vLLM 本地 / LiteLLM 网关 / 商用 API）。

    base_url 指向 …/v1；密钥从 api_key_env 环境变量读取，不落库不落明文。
    """

    def __init__(self, base_url: str, model: str, *, api_key_env: str | None,
                 timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout

    async def chat(self, messages: list[dict], *, max_tokens: int,
                   json_mode: bool = True) -> tuple[str, dict]:
        headers = {"Content-Type": "application/json"}
        import os
        key = os.environ.get(self.api_key_env or "", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions",
                                     json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return (data["choices"][0]["message"]["content"],
                data.get("usage") or {})


class CallableProvider:
    """测试/本地用：可调用对象作 provider（离线运行）。"""

    def __init__(self, fn, model: str = "callable-mock"):
        self._fn = fn
        self.model = model

    async def chat(self, messages: list[dict], *, max_tokens: int,
                   json_mode: bool = True) -> tuple[str, dict]:
        out = self._fn(messages)
        return out, {"prompt_tokens": 0, "completion_tokens": 0}


def provider_from_settings(settings) -> ChatProvider | None:
    """按运行配置构建 provider；未配置 base_url 时返回 None（本级整体跳过）。"""
    base_url = getattr(settings, "llm_base_url", None)
    if not base_url:
        return None
    return OpenAICompatibleProvider(
        base_url, getattr(settings, "llm_model", None) or "qwen3-8b",
        api_key_env=getattr(settings, "llm_api_key_env", None),
        timeout=getattr(settings, "llm_timeout", 60.0))


# ---- 质量门 1：判负规则 ----


def gate1_fail_reasons(meta: dict, detected_lang: str | None,
                       declared_lang: str | None,
                       raw_text_len: int) -> list[str]:
    """第一级清洗质量门判负原因清单；空列表 = 通过，不进第二级。

    判负条件（设计 4.2.1 的落地口径）：
    - 抽取链全失败或正文为空/过短（extract_failed / body_too_short）
    - 启发式语种检测与源声明语种分歧（language_mismatch，替代双分类器分歧）
    - 元数据低置信：无标题或无发布时间（metadata_low_confidence）
    """
    reasons: list[str] = []
    body = meta.get("body") or ""
    if meta.get("extractor") is None and not body:
        reasons.append("extract_failed")
    elif len(body) < 200:
        reasons.append("body_too_short")
    if detected_lang and declared_lang and detected_lang != declared_lang:
        # 启发式检测器只在识别出非拉丁文字系时给出非 fallback 结果
        reasons.append("language_mismatch")
    if not meta.get("title"):
        reasons.append("metadata_low_confidence")
    if raw_text_len < 100:
        reasons.append("raw_too_thin")
    return reasons


# ---- 确定性检查（schema 闸门之后，设计 4.2.1） ----


def _shingles(text: str, n: int = 8) -> set[str]:
    words = re.findall(r"\S+", (text or "").lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def body_overlap_ok(cleaned: str, raw_text: str, threshold: float = 0.5) -> bool:
    """清洗后正文须与输入文本高重叠（8-词 shingle 覆盖率 ≥50%）。

    防止模型臆造/续写：schema 只能保证字段形状，不能保证模型读对了源文本。
    """
    cleaned_shingles = _shingles(cleaned)
    if not cleaned_shingles:
        return False
    raw_shingles = _shingles(raw_text)
    hit = sum(1 for s in cleaned_shingles if s in raw_shingles)
    return hit / len(cleaned_shingles) >= threshold


def deterministic_check(cleaned: CleanedArticle, raw_text: str,
                        fetched_at: str) -> list[str]:
    """校验通过后的确定性检查；返回违规列表（空=通过）。"""
    errors: list[str] = []
    if not body_overlap_ok(cleaned.body, raw_text):
        errors.append("body_not_grounded_in_source")
    if cleaned.published_at and fetched_at and cleaned.published_at > fetched_at:
        errors.append("published_after_fetch")
    return errors


# ---- 清洗主流程：一次调用 + 纠错重试 ≤1 次 + DLQ ----


def _build_prompt(raw_text: str, meta: dict, reasons: list[str],
                  error_note: str | None = None) -> list[dict]:
    user = (
        f"第一级抽取失败原因：{', '.join(reasons)}。\n"
        f"已抽取元数据：title={meta.get('title')!r}, "
        f"published_at={meta.get('published_at')!r}, "
        f"authors={meta.get('authors')!r}。\n"
        f"网页原始文本（截断至 8000 字符）：\n{raw_text[:8000]}")
    if error_note:
        user += f"\n\n上一次输出未通过校验：{error_note}。请修正后重新输出 JSON。"
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


async def llm_clean_article(db_path: str, provider: ChatProvider, *,
                            raw_text: str, meta: dict, reasons: list[str],
                            fetched_at: str, article_id: int | None = None,
                            max_tokens: int = 2048) -> CleanedArticle | None:
    """对一篇判负文章执行 LLM 清洗；返回 CleanedArticle 或 None（进 DLQ）。

    流程：prompt → provider.chat → JSON 解析 → Pydantic 校验 → 确定性检查；
    任一环节失败以纠错 prompt 重试一次；再失败写 dead_letters。
    """
    error_note: str | None = None
    for attempt in range(2):
        messages = _build_prompt(raw_text, meta, reasons, error_note)
        try:
            text, usage = await provider.chat(messages, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001  # provider 为插件边界，任意失败都转重试/DLQ
            _record_call(db_path, "clean", provider.model, {}, False)
            error_note = f"provider 调用失败: {exc.__class__.__name__}"
            continue
        _record_call(db_path, "clean", provider.model, usage, True)
        try:
            cleaned = CleanedArticle.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            error_note = f"JSON/schema 校验失败: {exc}"
            continue
        violations = deterministic_check(cleaned, raw_text, fetched_at)
        if violations:
            error_note = f"确定性检查失败: {', '.join(violations)}"
            continue
        return cleaned
    # 重试耗尽 → 死信队列（保留输入摘要与错误，人工抽检/源规则修复）
    _to_dlq(db_path, "llm_clean", article_id,
            {"reasons": reasons, "meta": {k: meta.get(k) for k in
             ("title", "published_at", "authors", "extractor")},
             "raw_text_head": raw_text[:2000]},
            {"last_error": error_note})
    log.info("LLM 清洗失败进死信: article=%s err=%s", article_id, error_note)
    return None


def _record_call(db_path: str, stage: str, model: str,
                 usage: dict, success: bool) -> None:
    from .pipeline import utcnow
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            "INSERT INTO llm_calls (stage, model, prompt_tokens,"
            " completion_tokens, success, created_at) VALUES (?,?,?,?,?,?)",
            (stage, model, usage.get("prompt_tokens", 0),
             usage.get("completion_tokens", 0), int(success), utcnow()),
        )


def _to_dlq(db_path: str, stage: str, article_id: int | None,
            payload: dict, errors: dict) -> None:
    from .pipeline import utcnow
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            "INSERT INTO dead_letters (stage, article_id, payload, errors,"
            " status, created_at) VALUES (?,?,?,?,'open',?)",
            (stage, article_id, json.dumps(payload, ensure_ascii=False),
             json.dumps(errors, ensure_ascii=False), utcnow()),
        )
