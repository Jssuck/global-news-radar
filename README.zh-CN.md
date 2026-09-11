# Global News Radar（全球新闻媒体聚合监控平台）

**自托管、完全开源（MIT）的全球主流媒体 7×24 监控平台——媒体一更新，新闻即入库。**

[English](README.md) | 简体中文

---

## 项目简介

Global News Radar 锁定全球各国主流媒体——通讯社、全国性大报、公共广播电视——进行 24 小时不间断监控。媒体一旦发布新内容，平台立即抓取，经三级管线清洗整理后，形成可检索的结构化新闻流。

- **全球源注册表**——基于 GDELT 域名数据 × Media Cloud 国家级清单 × 人工策展 × 社区共建，构建约 1000–2500 家国家级主流媒体、覆盖 30–50 语种的种子源库
- **准实时抓取**——支持 WebSub 的源走推送，其余按条件 GET（ETag/Last-Modified）+ 按源自适应轮询，新文章分钟级入库
- **地域受限感知**——当媒体仅允许本国 IP 访问时，平台自动检测（HTTP 451、含地域语料的 403、地域重定向、正文截断），并在源卡片上以文字提示：「该媒体仅允许 xx 地区 IP 访问，需要添加 xx 地域的代理」。代理由用户自行配置（BYO Proxy），系统绝不静默切换出口
- **三级数据管线**——规则清洗（正文抽取、语种检测、去重）→ LLM 清洗（Schema 校验的结构化纠错）→ LLM 整理（摘要、翻译、实体抽取、主题打标、跨源事件归并）
- **全栈应用**——新闻流看板、源管理、代理配置、带管理员审核的用户注册体系、REST API

## 为什么再做一个新闻聚合？

现有开源工具各管一段：RSS 阅读器只读 feed，变更检测只检测变化。没有任何一个开源项目打通「全球源覆盖 + 准实时监控 + 文章级结构化清洗 + 地域受限处置」的完整闭环；而几个头部项目（RSSHub、FreshRSS、Firecrawl 核心）采用 AGPL/GPL，代码无法直接复用。Global News Radar 从第一天起采用最宽松的 MIT 许可证，并优先复用宽松许可证的最佳组件（trafilatura、newspaper4k、feedparser），不重复造轮子。

> **合规红线**：平台从不分发文章全文。仅提供标题、极短摘要与原文链接；始终遵守 robots.txt。

## 架构一览

```
源注册表 → 调度层（按源自适应） → 抓取 worker（HTTP + 无头浏览器兜底）
   │                │
   ▼                ▼
地域受限检测     消息队列
   │                │
   ▼                ▼
用户文字提示     三级处理管线
（添加 xx 地域代理）  规则清洗 → LLM 清洗 → LLM 整理
                        │
                        ▼
        PostgreSQL + pgvector · 对象存储 · 搜索引擎
                        │
                        ▼
              REST API ← 认证与审核 → Web 看板
```

完整设计文档：[docs/design/design-v1.0.md](docs/design/design-v1.0.md)

## 技术栈

| 层 | 选型 | 许可证 |
|---|---|---|
| 前端 | Next.js + TypeScript + Tailwind CSS + shadcn/ui | MIT |
| 后端与爬虫 | FastAPI（Python）+ httpx + Playwright | MIT / Apache-2.0 |
| 正文抽取 | trafilatura（主）+ newspaper4k/readability（兜底） | Apache-2.0 / MIT |
| 队列 | Celery + RabbitMQ（网络服务） | BSD / MPL-2.0 |
| 存储 | PostgreSQL + pgvector · Meilisearch · MinIO/S3 · Valkey | PostgreSQL / Apache-2.0 / MIT / AGPL(服务) / BSD |
| LLM | 可插拔 Provider：默认本地开源模型（Qwen 等），可选 OpenAI 兼容 API | — |
| 认证 | Better Auth + Casbin RBAC，Mastodon 式注册审核 | MIT / Apache-2.0 |
| 部署 | Docker Compose，`.env` 一键自托管 | — |

## 项目状态

**孵化期（M1 已完成，M2/M3 功能已落地，门禁待测）**。v1.0 设计已定稿，抓取内核已在 `main`：443 个种子源（60+ 国、40+ 语种）、RSS→sitemap 发现降级、robots.txt 合规、per-host 礼貌限速、自适应轮询、trafilatura 抽取兜底链——门禁结论见 [M1 门禁报告](docs/harness/gate-reports/m1-gate-report.md)（PASS）。

v0.1.0 之后单进程应用已新增：三级代理绑定（源>国>全局）注入抓取路径、geo 中信号+他国出口对照确认、受限源 GDELT 聚合层降级、可插拔 LLM 清洗（schema 闸门+确定性检查+DLQ+成本记录）、两遍 KNN+UnionFind 事件聚类与 LLM 事件整理、Playwright 渲染兜底（日预算限制）、session/API-key 认证与注册审核三态、RBAC 与限流、35 个 REST 端点（含 SSE 事件流）以及三个服务端渲染界面。M2/M3 正式门禁测量待执行。

### 路线图

| 里程碑 | 范围 | 目标 |
|---|---|---|
| M0 筹备 | 仓库、治理、CI、harness 文档 | —（已完成） |
| M1 抓取内核 | 种子源库、调度、RSS/sitemap 抓取、规则清洗 | ✅ 已完成——440 源入库，抽取成功率 95.8% |
| M2 地域与 LLM 管线 | 地域受限检测+代理提示、LLM 清洗与整理 | 🚧 已实现——黄金集准确率与 72h 指标待测 |
| M3 全栈应用 | 看板、认证审核、REST API v1 | 🚧 35 个端点已上线（含 SSE）；契约测试待补 |
| M4 加固与发布 | 可观测性、文档、发布 | 🚧 代码就绪——契约测试/故障注入/Dockerfile/安全扫描已落地；72h 与千源实测、部署演练待执行 |

验收标准与门禁评审按 [docs/harness/](docs/harness/) 强制执行。

## Harness 框架（项目不烂尾的工程化保障）

本仓库内置完整的项目级 harness——管控进度、质量与发布的「工程宪法」：

- [00 · 总览](docs/harness/00-overview.md)——角色、四大支柱、文档关系
- [01 · 生命周期](docs/harness/01-lifecycle.md)——项目/版本/里程碑/安全响应生命周期
- [02 · 进度把控](docs/harness/02-progress-control.md)——双周迭代、门禁评审、KPI、黄红灯升级
- [03 · 验收标准](docs/harness/03-acceptance-criteria.md)——M0–M4 量化门禁与 Definition of Done
- [04 · 代码质量](docs/harness/04-code-quality.md)——lint/测试/评审 rubric、CI 门禁、许可证规则
- [05 · 执行与工具化](docs/harness/05-harness-enforcement.md)——GitHub Actions 工作流、`gate_check.py` 契约

## 项目级 Skills

将项目运维知识固化为可复用的 agent skills（人类可当 runbook 用，AI agent 可直接加载执行）：

| Skill | 用途 |
|---|---|
| [`skills/news-source-onboarding`](skills/news-source-onboarding) | 媒体源接入五步流程（元数据 schema、四级发现降级、验收标准）+ `validate_source.py` |
| [`skills/geo-block-triage`](skills/geo-block-triage) | 地域封锁 vs 反爬 vs 法律封锁的计分判定、用户提示文案模板、代理绑定规则 + `geo_verdict.py` |
| [`skills/cleaning-pipeline-qc`](skills/cleaning-pipeline-qc) | 三级管线质量闸门与周抽检、黄金样本回归、DLQ 处置 + `qc_sample.py` |
| [`skills/milestone-gate-review`](skills/milestone-gate-review) | 门禁评审流程、证据规则、结论分级 + `gate_check.py` |

## 快速开始（MVP）

最小端到端原型在 [`mvp` 分支](https://github.com/Jssuck/global-news-radar/tree/mvp)：种子源 → RSS 抓取 → trafilatura 抽取 → 去重 → 地域受限提示 → FastAPI 端点 → 简易看板。

```bash
git clone -b mvp https://github.com/Jssuck/global-news-radar.git
cd global-news-radar
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000
```

## 参与贡献

欢迎补充媒体源（最简单的首次贡献——一个媒体一个 YAML 文件）、解析器、翻译与核心代码。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [harness 文档](docs/harness/)；所有 PR 必须通过代码质量门禁。媒体源采用「源维护者责任制」社区共建模式，借鉴 RSSHub 的路由贡献机制。

## 许可证

[MIT](LICENSE) © Global News Radar contributors。许可证仅覆盖代码——抓取的新闻内容版权归原出版者所有，本平台从不分发全文。
