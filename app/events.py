"""第三级：embedding 事件聚类 + LLM 事件整理（设计 4.3，M2 落地）。

聚类：两遍 KNN + UnionFind——
  第一遍：窗口内（默认 72h）未归属文章对全窗口向量做 KNN，cosine ≥ 阈值即并集；
  第二遍：事件质心（成员向量均值）两两比对，高相似合并事件。
整理：事件成员 ≥2 才送第三级 LLM（设计 4.3.2：单文章事件不值得 LLM 成本）；
  LLM 不可用/失败时保留 organized=0 占位（降级但数据不丢）。
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, ValidationError

from .db import WRITE_LOCK, connect
from .embed import (
    article_text,
    blob_to_vec,
    cosine,
    embedder_from_settings,
    save_embedding,
)
from .llm import _record_call, _to_dlq, provider_from_settings

log = logging.getLogger("gnr.events")

CLUSTER_WINDOW_HOURS = 72
KNN_K = 5
SIM_THRESHOLD = 0.78          # 第一遍：篇级相似度并集阈值
MERGE_THRESHOLD = 0.85        # 第二遍：事件质心合并阈值（更严格）
MIN_EVENT_SIZE_FOR_LLM = 2    # 单文章事件不送第三级 LLM


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


class OrganizedEvent(BaseModel):
    """第三级 LLM 事件整理输出闸门。"""
    title: str = Field(min_length=1)
    summary: str
    iptc_tags: list[str] = Field(default_factory=list)
    heat_score: float = Field(ge=0.0, le=1.0)


ORGANIZE_PROMPT = (
    "你是新闻事件编辑。输入是同一新闻事件的多篇报道（标题+摘要，可能多语种）。"
    "只输出一个 JSON 对象，字段：title(中文事件名), summary(≤200字事件综述), "
    "iptc_tags(IPTC 主题码名称数组), heat_score(0-1 事件热度，"
    "按媒体影响力与报道数量估计)。不得加入输入之外的事实。")


async def embed_pending_articles(db_path: str, embedder, now: str,
                                 batch: int = 64) -> int:
    """给窗口内尚无向量的文章补 embedding；返回新增条数。"""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.title, a.summary FROM articles a
            LEFT JOIN article_embeddings e ON e.article_id = a.id
            WHERE e.article_id IS NULL AND a.degraded = 0
              AND a.title IS NOT NULL
            ORDER BY a.id DESC LIMIT ?
            """,
            (batch,),
        ).fetchall()
    if not rows:
        return 0
    texts = [article_text(dict(r)) for r in rows]
    try:
        vecs = await embedder.embed(texts)
    except Exception:  # embedder 插件边界：失败跳过本轮
        log.exception("embedding 调用失败，本轮跳过")
        return 0
    for row, vec in zip(rows, vecs):
        save_embedding(db_path, row["id"], vec, embedder.model, now)
    return len(rows)


def _window_articles(conn, since: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.id, a.title, a.event_id, e.embedding
        FROM articles a JOIN article_embeddings e ON e.article_id = a.id
        WHERE a.fetched_at >= ? AND a.degraded = 0
        """,
        (since,),
    ).fetchall()
    return [dict(r) for r in rows]


def cluster_events(db_path: str, now: str, *,
                   window_hours: int = CLUSTER_WINDOW_HOURS,
                   k: int = KNN_K, threshold: float = SIM_THRESHOLD) -> int:
    """两遍 KNN+UnionFind 聚类；返回活跃事件数。

    第一遍篇级并集 → 生成/更新事件；第二遍质心合并相邻事件。
    """
    since = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat(
        timespec="seconds")
    with connect(db_path) as conn:
        arts = _window_articles(conn, since)
    if len(arts) < 2:
        return 0
    vecs = {a["id"]: blob_to_vec(a["embedding"]) for a in arts}
    ids = [a["id"] for a in arts]

    # 第一遍：篇级 KNN + UnionFind
    uf = UnionFind()
    for aid in ids:
        sims = sorted(
            ((cosine(vecs[aid], vecs[oid]), oid) for oid in ids if oid != aid),
            reverse=True)[:k]
        for sim, oid in sims:
            if sim >= threshold:
                uf.union(aid, oid)

    groups: dict[int, list[int]] = {}
    for aid in ids:
        groups.setdefault(uf.find(aid), []).append(aid)

    # 第二遍：事件质心合并
    centroids = {}
    for root, members in groups.items():
        n = len(vecs[members[0]])
        centroids[root] = [sum(vecs[m][i] for m in members) / len(members)
                         for i in range(n)]
    roots = list(groups)
    euf = UnionFind()
    for i, r1 in enumerate(roots):
        for r2 in roots[i + 1:]:
            if cosine(centroids[r1], centroids[r2]) >= MERGE_THRESHOLD:
                euf.union(r1, r2)
    merged: dict[int, list[int]] = {}
    for r in roots:
        merged.setdefault(euf.find(r), []).extend(groups[r])

    # 落库：事件行 upsert + 文章归属
    n_events = 0
    with WRITE_LOCK, connect(db_path) as conn:
        for members in merged.values():
            ph = ",".join("?" * len(members))
            existing = conn.execute(
                f"SELECT DISTINCT event_id FROM articles"
                f" WHERE id IN ({ph}) AND event_id IS NOT NULL",
                members,
            ).fetchall()
            if existing:
                event_id = min(r["event_id"] for r in existing)
                conn.execute(
                    "UPDATE events SET article_count=?, last_updated=?"
                    " WHERE id=?",
                    (len(members), now, event_id))
            else:
                cur = conn.execute(
                    "INSERT INTO events (article_count, first_seen,"
                    " last_updated, organized) VALUES (?,?,?,0)",
                    (len(members), now, now))
                event_id = cur.lastrowid
            conn.execute(
                f"UPDATE articles SET event_id=? WHERE id IN ({ph})",
                [event_id, *members])
            n_events += 1
    return n_events


async def organize_events(db_path: str, provider, now: str,
                          *, limit: int = 20) -> int:
    """第三级 LLM 整理：为 organized=0 且成员达标的事件生成标题/摘要/标签/热度。

    LLM 不可用（provider=None）或输出不合格时保留 organized=0 占位，
    失败事件进 dead_letters（stage=organize）。
    """
    if provider is None:
        return 0
    with connect(db_path) as conn:
        events = conn.execute(
            "SELECT * FROM events WHERE organized = 0"
            " AND article_count >= ? ORDER BY article_count DESC LIMIT ?",
            (MIN_EVENT_SIZE_FOR_LLM, limit),
        ).fetchall()
    done = 0
    for ev in events:
        with connect(db_path) as conn:
            members = conn.execute(
                """
                SELECT a.title, a.summary, s.name AS source_name
                FROM articles a JOIN sources s ON s.id = a.source_id
                WHERE a.event_id = ? ORDER BY a.id LIMIT 10
                """,
                (ev["id"],),
            ).fetchall()
        user = "\n".join(
            f"- [{m['source_name']}] {m['title']}：{(m['summary'] or '')[:100]}"
            for m in members)
        try:
            text, usage = await provider.chat(
                [{"role": "system", "content": ORGANIZE_PROMPT},
                 {"role": "user", "content": user}], max_tokens=512)
        except Exception:  # noqa: BLE001  # provider 插件边界
            _record_call(db_path, "organize", provider.model, {}, False)
            continue
        _record_call(db_path, "organize", provider.model, usage, True)
        try:
            org = OrganizedEvent.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            _to_dlq(db_path, "organize", None,
                    {"event_id": ev["id"], "raw": text[:1000]},
                    {"error": str(exc)})
            continue
        with WRITE_LOCK, connect(db_path) as conn:
            conn.execute(
                "UPDATE events SET title=?, summary=?, iptc_tags=?,"
                " heat_score=?, organized=1, last_updated=? WHERE id=?",
                (org.title, org.summary, json.dumps(org.iptc_tags),
                 org.heat_score, now, ev["id"]),
            )
        done += 1
    return done


async def organize_cycle(db_path: str, settings, *,
                         embedder=None, organizer=None) -> dict:
    """一轮整理：补 embedding → 聚类 → 事件整理；返回各步计数。"""
    from .pipeline import utcnow
    now = utcnow()
    if embedder is None:
        embedder = embedder_from_settings(settings)
    embedded = clustered = 0
    if embedder is not None:
        embedded = await embed_pending_articles(db_path, embedder, now)
        clustered = cluster_events(db_path, now)
    if organizer is None:
        organizer = provider_from_settings(settings)
    organized = await organize_events(db_path, organizer, now)
    return {"embedded": embedded, "clustered_events": clustered,
            "organized": organized}
