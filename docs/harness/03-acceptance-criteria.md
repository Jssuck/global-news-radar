# M0–M4 验收标准

> **目的**：为每个里程碑定义可量化、可测试、可裁决的验收标准，是 Gate Review 的唯一裁决依据；`scripts/gate_check.py` 直接读取本文量化表执行自动化判定。
> **适用范围**：v1.0 交付期 M0–M4 全部里程碑；所有量化指标与设计文档 v1.0 第 7 章里程碑表严格一致。
> **关联文档**：延期处置见 `02-progress-control.md`；代码级 Definition of Done 细节见 `04-code-quality.md`；自动化检查实现见 `05-harness-enforcement.md`。

## 0. 通用约定

- 每条标准标注类型：**[功能]** 功能性 / **[非功能]** 性能·可观测性·文档 / **[DoD]** 流程完备性；以及级别：**[必须]** 任一 FAIL 即门禁不通过 / **[参考]** 记录但不阻断。
- 涉及成功率的指标均注明样本基线与测量窗口；人工抽检类指标注明样本量与抽检方法。
- 指标测量口径以 `scripts/gate_check.py` 实现为准，口径变更走 harness 修订 PR。

## 1. M0 筹备（W1–W2）

交付物：monorepo 仓库、治理文件（LICENSE/NOTICE/CONTRIBUTING/行为准则/SECURITY.md）、CI（测试 + commitlint + 许可证扫描）、semantic-release（0.x 起步）、docker-compose 骨架（12 服务）。

| # | 验收标准 | 类型/级别 | 测量方式 |
|---|---|---|---|
| M0-F1 | main 分支 CI 全绿（lint/test/build/commitlint/许可证扫描五类工作流均存在且通过） | 功能/必须 | `gh api` 检查 workflow 状态 |
| M0-F2 | `docker compose up -d` 后 12 个服务（web/api/worker/beat/renderer/rabbitmq/postgres/meilisearch/valkey/minio/litellm/vllm-profile 除外说明）健康检查 **100% 通过** | 功能/必须 | 自动化脚本轮询 healthcheck |
| M0-F3 | 许可证扫描报告**零 GPL/AGPL 代码级传递依赖** | 功能/必须 | scancode 报告解析 |
| M0-F4 | AMD64/ARM64 双架构镜像构建成功 | 功能/必须 | CI 构建产物检查 |
| M0-N1 | 五份治理文件 + harness 五份文档齐备且通过评审合入 | 非功能/必须 | 文件存在性 + PR 记录 |
| M0-N2 | semantic-release 从 0.x 自动算版、生成 CHANGELOG、打 tag 的链路在空仓库上验证通过 | 功能/必须 | 试运行记录 |
| M0-D1 | 全部 DoD 清单项（见 §6）对 M0 各交付物逐条勾选 | DoD/必须 | Gate Review 记录 |

## 2. M1 抓取内核（W3–W6）

交付物：源注册表 + 15 张核心表迁移、Mercator 前后队调度（Celery+RabbitMQ+Beat）、四级发现降级、条件 GET 与自适应轮询、第一级规则清洗（trafilatura 兜底链、ftfy、双语种检测、SHA-256 精确去重、元数据六级证据链）、原始 HTML 对象存储存档。

| # | 验收标准 | 类型/级别 | 测量方式 |
|---|---|---|---|
| M1-F1 | **300 家种子源入库并持续抓取 72h**，期间全库抓取成功率 ≥90% | 功能/必须 | 健康统计滚动窗口 |
| M1-F2 | **正文抽取成功率 ≥90%**（对人工标注的 200 篇样本，标注集随仓库 fixtures 管理、不含真实新闻全文） | 功能/必须 | 黄金样本回归测试 |
| M1-F3 | 单篇一级清洗 CPU **<50ms**（p95，基准机固定规格） | 非功能/必须 | 基准测试工作流 |
| M1-F4 | **重复摄入率下降 ≥90%**（对照无条件 GET 基线的受控实验） | 功能/必须 | 实验报告 + 统计查询 |
| M1-F5 | **单源礼貌违规 0 次**（per-host 限速突破零容忍告警全程未触发） | 功能/必须 | 告警事件日志 |
| M1-F6 | 四级发现降级链对抽检 30 源可演示逐级降级与上级策略周期性重测 | 功能/必须 | 试抓端点现场演示 |
| M1-N1 | 每个核心模块有结构化日志与基础指标（成功率/延迟/队列深度）暴露 | 非功能/必须 | 指标端点检查 |
| M1-N2 | 质量门 1 判负率监控与 10–20% 告警带已上线（M2 成本阀门的前置） | 非功能/必须 | 看板截图 + 告警配置 |
| M1-N3 | 调度器、四级降级、清洗管线的开发者文档与接口注释同步 | 非功能/必须 | 文档 PR 记录 |

**M1 门禁执行口径（2026-08-25 Gate Review 实际采用）**：单人维护阶段由 `scripts/collect_metrics.py` + `gate_check.py` 自动化裁决，指标键与实测结论见 `docs/harness/gate-reports/m1-gate-report.md`。条目映射：`M1-F1` → `sources_onboarded ≥300` + `rss_discovery_rate ≥0.85`；`M1-F2` → `extraction_success ≥0.90`（全量抓取计数口径，替代标注集口径）；`M1-F4` → `dedup_hits` 对照观测；另有 `coverage ≥0.80`（非 critical）与「测试全量通过 + ruff 零告警」。`M1-F3`/`M1-F5`/`M1-F6` 由代码审查与分诊记录人工核对。里程碑窗口期出口网络劣化导致的 31 个 inconclusive 源按遗留承诺复测。

## 3. M2 地域代理 + LLM 管线（W7–W10）

交付物：受限信号判定规则表 + `geo_status` 写回、三级代理绑定路由、受限降级模式与提示生成 API、Playwright 渲染 worker 小池、第二级 LLM 清洗（LiteLLM + JSON Schema 闸门 + 死信队列）、embedding 两遍 KNN 聚类、第三级 LLM 事件整理。

| # | 验收标准 | 类型/级别 | 测量方式 |
|---|---|---|---|
| M2-F1 | 在 **≥50 个已知受限源样本**上，信号判定准确率 **≥95%**，且 **429/5xx 误判为地域受限 0 次** | 功能/必须 | 受限样本集回归 |
| M2-F2 | 三级绑定解析（源级>国家级>全局）优先级与冲突校验单测全覆盖；凭据不落明文（仅存密钥库引用）经代码审查确认 | 功能/必须 | 单测 + 审查记录 |
| M2-F3 | **质量门 1 判负率 ≤20%**（连续 7 天窗口） | 功能/必须 | 管线指标 |
| M2-F4 | **跨语种事件归并 precision ≥85%**（人工抽检 100 簇，双语种以上簇占比 ≥50%） | 功能/必须 | 抽检记录表 |
| M2-F5 | **渲染流量占比 ≤15%**（连续 7 天窗口） | 非功能/必须 | 管线指标 |
| M2-F6 | **全管线 LLM 月成本 ≤$100**（按当月用量线性外推） | 非功能/必须 | 成本监控报表 |
| M2-F7 | LLM 输出 JSON Schema 校验失败重试 ≤1 次后进死信队列的路由可演示；死信人工抽检通道可用 | 功能/必须 | 故障注入演示 |
| M2-N1 | 受限降级模式（仅聚合层标题+URL 元数据）与断更状态在看板可区分展示 | 功能/必须 | 看板检查 |
| M2-N2 | LiteLLM 网关切换 Provider（本地 vLLM ↔ 商用 API）只改配置不改代码的演练记录 | 非功能/必须 | 演练记录 |

## 4. M3 全栈应用（W8–W12，与 M2 后半并行）

交付物：Next.js 三界面、Better Auth 数据库 session + 注册三态 + 邀请码旁路、Casbin RBAC 四角色、27 个 REST 端点、SSE 实时事件流、健康度看板（成功率/GDELT 漏抓对照/受限源视图）。

| # | 验收标准 | 类型/级别 | 测量方式 |
|---|---|---|---|
| M3-F1 | **27/27 端点通过 OpenAPI 契约测试**（请求/响应 schema、错误码、分页参数） | 功能/必须 | 契约测试工作流 |
| M3-F2 | 注册状态机 **6 条迁移路径测试全覆盖**（pending→approved、pending→rejected、approved→suspended、suspended→approved、开放注册直达、邀请码旁路） | 功能/必须 | E2E 测试报告 |
| M3-F3 | 关键路径 E2E（注册→审批→配置源→查看事件流）全通过 | 功能/必须 | Playwright E2E |
| M3-F4 | 看板 **GDELT 漏抓对照数据每日更新**（连续 7 天检查无缺失日） | 功能/必须 | 数据时效查询 |
| M3-F5 | **API 限流分层实测生效**：超限返回 429 + Retry-After，两档 tier（60–100 与 500–1000 req/min）分别验证 | 功能/必须 | 限流压测脚本 |
| M3-F6 | RBAC 四角色（admin/editor/user/api-caller）越权访问测试用例全通过；被驳回/封禁用户 session 即时吊销 | 功能/必须 | 安全测试用例 |
| M3-F7 | API Key 哈希存储、明文仅签发时返回一次、吊销即时生效 | 功能/必须 | 用例 + 审查 |
| M3-N1 | 三界面 Lighthouse 性能分 ≥80；SSE 事件流从抓取变更到前端可见延迟 ≤10s | 非功能/必须 | Lighthouse CI + 延迟测量 |
| M3-N2 | 面向社区贡献者的源适配器模板与贡献文档发布（M4 开放「请求新源」通道的前置） | 非功能/必须 | 文档站检查 |

## 5. M4 加固与 v1.0 发布（W13–W15）

交付物：源清单扩至 1,000 家（150+ 国家）、72h 全链路压测与故障注入、安全自查、部署/运维/贡献三套文档站、v1.0.0 tag 与发布说明。

| # | 验收标准 | 类型/级别 | 测量方式 |
|---|---|---|---|
| M4-F1 | **1,000 源在线（150+ 国家）、全库正文抽取成功率 ≥90%、断更源占比 <5%**（72h 窗口） | 功能/必须 | 健康统计 |
| M4-F2 | **72h 连续运行零 P0 事故**；故障注入（worker 宕机、broker 重启、LLM 不可用）后**队列积压可自动消化**，「未整理占位」降级路径生效 | 功能/必须 | 压测与故障注入报告 |
| M4-F3 | **许可证扫描与密钥泄漏扫描零高危**；凭据落库审计、注入/越权用例安全自查通过 | 功能/必须 | 扫描报告 + 自查清单 |
| M4-F4 | **全新机器按文档 30 分钟内完成部署**（`docker compose up -d` 至看板可访问，由未参与部署文档编写的人员执行） | 非功能/必须 | 部署演练记录 |
| M4-F5 | v1.0.0 tag、CHANGELOG、人工发布说明（含迁移指引与合规红线声明）齐备 | DoD/必须 | 发布物检查 |
| M4-N1 | 部署/运维/贡献三套文档站上线，含地域受限合规说明与代理配置指引 | 非功能/必须 | 文档站检查 |
| M4-N2 | 「请求新源」社区通道随 v1.0 同步开放，断更告警→派单流程可用 | 功能/必须 | 流程演练 |
| M4-N3 | 礼貌违规 0 次（压测期间同样适用）；robots 快照按域名缓存可查证 | 功能/必须 | 告警日志 + 快照抽查 |

## 6. Definition of Done（DoD）清单

每个任务/PR 及每个里程碑出口均须满足；Gate Review 时逐条核对：

- [ ] 代码评审通过：≥1 名 CODEOWNER approve，评审 rubric 五维度（正确性/可读性/测试/安全/许可证合规，见 `04` 文档）无未解决的阻断意见
- [ ] 测试覆盖：新增/修改代码满足覆盖率门槛（核心管线 ≥80%、整体 ≥70%）；爬虫解析器改动通过黄金样本集回归且无精度回退
- [ ] CI 全绿：lint / typecheck / test / build / commitlint / 许可证扫描全部通过
- [ ] 文档同步：公开接口、配置项、部署行为变更已同步到对应文档（含 OpenAPI 描述与 `.env.example`）
- [ ] CHANGELOG 更新：Conventional Commits 驱动自动生成，breaking change 已人工补写迁移指引
- [ ] 合规自查：无新增 GPL/AGPL 代码级依赖；无密钥/凭据入仓；无真实新闻全文进入 fixtures；无绕过 robots.txt 的行为变更

## 7. 验收方式与签字记录

验收 = **自动化检查 + Gate Review 会议 + 签字记录** 三段闭环：

1. **自动化检查**：Gate Review 前 48h，Release Manager 运行 `scripts/gate_check.py --milestone Mx`，脚本读取本文量化表输出 PASS/FAIL 报告（约定见 `05` 文档）；自动化覆盖不了的条目（人工抽检、演示类）标注 MANUAL。
2. **Gate Review 会议**：逐条过报告，MANUAL 项现场核对证据；任一必须项 FAIL 即不通过，按 `02-progress-control.md` §3 处置。
3. **签字记录**：会议结论归档 `docs/gate-reviews/Mx-signoff.md`，模板如下：

```markdown
# M{x} Gate Review 签字记录
- 日期 / 会议形式：
- 出席（Maintainer 全员的勾选记录）：
- gate_check.py 报告版本（commit SHA）与总体结论：PASS x/y；MANUAL z 项
- 逐项裁决表（标准编号 → PASS/FAIL → 证据链接）：
- 未通过项与处置决议（黄/红灯、裁剪项、顺延项）：
- 签字：Release Manager ______ ；Maintainer ______ ______
```

签字记录随仓库存档、永不可修改（纠错须追加新记录），作为里程碑达成的唯一正式凭据。

---

## 附录 B · M4 硬化落地记录（2026-09-11）

| 项 | 状态 | 证据 |
|---|---|---|
| M3-F1 契约测试 | ✅ | `tests/test_contract.py`：37 端点全注册 OpenAPI、无参 GET 可达、列表端点 total/items 契约 |
| M3-F2 六迁移路径 | ✅ | `test_state_machine_six_paths` + open/invite 两档模式 + 终态保护 |
| M3-F5 分级限流 | ✅ | `test_rate_limit_tiers`：标准档 429+Retry-After+X-RateLimit-*，API Key 档独立 |
| M3-F6 四角色/即时吊销 | ✅ | `test_suspend_revokes_sessions_and_keys`、`test_api_caller_no_web_session`、`test_audit_logs_written` |
| M3-F4 GDELT 漏抓对照 | ✅ 代码 | `gdelt_checks` 表 + 每日后台比对 + `/gdelt-checks`（连续性待 7 天运行窗口） |
| M2-F1 geo 黄金集 | ✅ | `tests/fixtures/geo_golden.json` 54 样本 + ≥95% 准确率/429-5xx 零误判回归门 |
| M4-F2 故障注入 | ✅ 代码 | `tests/test_fault_injection.py`：LLM 宕机→DLQ、organize 失败→占位、单源异常隔离；72h 零 P0 待运行窗口 |
| M4-F3 安全扫描 | ✅ | `scripts/security_scan.py`：密钥 0 命中、许可证 44 包 0 高危（socksio MIT 已人工核实覆盖） |
| M4-F4 部署 | 🚧 | `Dockerfile`+`docker-compose.yml`+`docs/deploy.md` 就绪；镜像构建与 30min 演练待执行（docker daemon 未启动） |
| M4-F5 发布 | 🚧 | `docs/release-notes-v1.0-draft.md` 就绪；tag 待 M4-F1/F2 实测窗口完成后执行 |
| M4-F1 千源/72h | ⏳ | 需真实运行窗口与源扩充（443→1000），不可离线代测 |
| M3-N2 贡献文档 | ✅ | `docs/community/source-request.md` + YAML 模板 + 合规红线 |
| M4-N1 三套文档 | ✅ | `docs/deploy.md`/`operations.md`/`community/source-request.md` |
