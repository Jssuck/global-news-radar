# Global News Radar · MVP 规格说明

**分支**：`mvp`　**定位**：单进程可跑通的最小可运行原型，用于快速验证 v1.0 设计的核心链路

## 1. MVP 范围

本 MVP 验证了设计的核心数据通路：**种子源 → RSS 轮询（条件 GET）→ 一级规则清洗 → 精确去重 → 地域受限检测与提示 → SQLite 存储 → REST API + 简易页面**。

| 模块 | 实现 | 位置 |
|---|---|---|
| 种子源库 | 14 个真实主流媒体 RSS（11 国、8 语种），通过 `validate_source.py` 校验 | `sources/*.yaml` |
| 抓取循环 | 后台 asyncio 任务按源级间隔轮询（默认 10 分钟）；ETag/Last-Modified 条件 GET，304 零成本跳过 | `app/poller.py`、`app/pipeline.py` |
| 一级规则清洗 | trafilatura 正文/元数据抽取；URL 规范化 + SHA-256 精确去重（`articles.url_hash` 唯一约束）；字符集启发式语种检测 | `app/cleaner.py` |
| 地域受限检测 | 移植 `skills/geo-block-triage/scripts/geo_verdict.py` 判定逻辑：HTTP 451、403+地域语料为强信号；429/5xx/挑战页显式排除 | `app/geo.py` |
| 存储 | SQLite 三表：`sources` / `articles` / `fetch_log`；原始 HTML 不落库，仅存正文与元数据 | `app/db.py` |
| REST API | `GET /api/v1/sources`（含 geo_status+hint）、`GET /api/v1/articles`（分页/过滤/q LIKE）、`GET /api/v1/articles/{id}`、`POST /api/v1/sources/{id}/check`、`GET /api/v1/stats`；自动 OpenAPI 于 `/docs` | `app/api.py` |
| 页面 | `/` 新闻流（标题+来源+时间+200 字符摘要+原文链接，不展示全文）；`/sources` 源看板（受限源提示 + 代理配置只读展示） | `app/pages.py`、`app/templates/` |
| 地域代理提示 | 受限源在 API 与页面输出「该媒体仅允许 xx 地区 IP 访问，需要添加 xx 地域的代理」文案；`config/proxies.example.yaml` 给出按国家/源绑定代理的格式（只读展示，不做真实转发） | `app/geo.py`、`app/proxyconf.py` |
| 测试 | pytest 37 项，全部离线：URL 规范化/去重、geo 判定（mock 响应）、管线（MockTransport）、API 冒烟（TestClient）、种子源 schema 校验 | `tests/` |

## 2. 运行与验证

```bash
pip install -r requirements.txt        # 运行依赖
pip install -r requirements-dev.txt    # 测试依赖（pytest、ruff）
uvicorn app.main:app --reload          # http://127.0.0.1:8000
python -m pytest tests -q              # 离线测试
ruff check app tests                   # 代码检查
```

配置项见 `.env.example`（`GNR_POLL_INTERVAL`、`GNR_DB_PATH`、`GNR_PROXY_CONFIG` 等）。

## 3. 简化点清单（相对 v1.0 设计）

1. **发现层只有 RSS**：四级降级（RSS→sitemap→HTML 列表页→聚合层）只实现第一级。
2. **无自适应轮询与 WebSub**：间隔取源 YAML 的 `estimated_interval`，无变更驱动伸缩。
3. **语种检测简化**：未引入 fastText/Lingua 双分类器，用字符集占比启发式 + 源声明语种兜底。
4. **去重只有第一段**：仅 URL 规范化 + SHA-256 精确去重，无 MinHash/LSH 近重与跨语种 embedding 聚类。
5. **无抽取兜底链**：仅 trafilatura 主抽取，无 newspaper4k/readability 兜底与 Fundus 式定制 parser；无 ftfy 编码修复。
6. **geo 判定只取强信号**：451 与 403+语料直接判定；中信号（警告页重定向/正文截断/多出口对照）未实现；`required_region` 直接取源所属国家，未经对照实验反推。
7. **代理只读不转发**：`config/proxies.yaml` 仅解析展示，httpx 请求不走代理；三级绑定解析未接进抓取路径。
8. **单进程无队列**：asyncio 后台任务替代 Celery+RabbitMQ；SQLite 替代 PostgreSQL+pgvector；无对象存储（HTML 本就不落库，仅无不可变存档与重放能力）。
9. **无 LLM 二三级**：LLM 清洗/整理、事件归并、摘要翻译全部不在 MVP 范围。
10. **无认证与多用户**：API 与页面无鉴权，无注册审核/RBAC；合规红线仅靠「不输出全文」的默认姿态保证。
11. **无礼貌爬取完整实现**：无 robots.txt 检查、无 per-host 令牌桶，仅有真实 UA 与固定间隔。

## 4. 与 v1.0 设计的差距（后续里程碑映射）

| 差距 | 对应设计章节 | 建议里程碑 |
|---|---|---|
| 四级发现降级 + 自适应轮询 + WebSub | 3.2、5.2.3 | M1 |
| 抽取兜底链、双语种检测、去重二/三段、元数据六级证据链 | 4.1 | M1–M2 |
| geo 中信号对照确认、三级代理绑定接入抓取路径 | 3.3、5.2.2 | M2 |
| LLM 清洗/整理、事件聚类 | 4.2、4.3 | M2 |
| 前端三界面、认证审核、27 端点、SSE | 5.4、5.5 | M3 |
| PostgreSQL+pgvector、对象存储、搜索引擎、队列化 | 5.3、6 | M1–M4 |
