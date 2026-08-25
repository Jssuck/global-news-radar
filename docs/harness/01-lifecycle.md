# 项目生命周期设计

> **目的**：为项目整体、版本、里程碑、安全事件四类对象定义阶段模型与流转条件，使「项目处于什么状态、下一步该做什么」成为可裁决的问题。
> **适用范围**：Global News Radar 仓库及其全部发布物；里程碑部分锁定设计文档 v1.0 第 7 章 M0–M4 排期。
> **关联文档**：出口门禁的量化裁决见 `03-acceptance-criteria.md`；进度偏离的处置见 `02-progress-control.md`；安全流程落地见仓库 `SECURITY.md`。

## 1. 项目阶段模型

项目整体沿 **孵化（Incubating）→ 活跃（Active）→ 维护（Maintenance）→ 归档（Archived）** 单向流转，允许「维护 → 活跃」的逆向激活，不允许跳级。

| 阶段 | 进入条件 | 退出条件（进入下一阶段） | 阶段特征 |
|---|---|---|---|
| Incubating | 仓库创建即进入（M0 起点） | v1.0.0 发布且 M4 Gate Review 签字通过 → Active | 版本 0.x，公共 API 不稳定；治理文件、CI、harness 文档在本阶段建立 |
| Active | v1.0.0 发布；核心团队 ≥3 人保持响应 SLA | 连续 2 个季度无 minor 版本且核心团队决议转向 → Maintenance | 功能迭代与源清单扩张并行；双周迭代节奏全开 |
| Maintenance | Maintainer 会议决议 + 公告 ≥30 天 | 连续 12 个月仅有个位数 commit、安全修复无人执行、或核心团队 <2 人且无继任者 → Archived；新增重大功能线且团队恢复 ≥3 人 → 可回 Active | 只接受 bugfix、安全修复与源适配器维护；不再规划新 minor 特性 |
| Archived | Maintainer 决议 + README 归档公告 | 不可逆；复活须由新团队 fork | 仓库只读；文档站保留合规红线说明；发布最后的维护终止版本 |

阶段判定责任：Maintainer 会议每季度结合 `02-progress-control.md` 的 KPI 做一次阶段健康评估，阶段流转须以 GitHub Discussion 公告并记入 CHANGELOG。设计文档第 8.4 节风险 R10（社区断流致项目僵化）的缓解动作——低门槛适配器模板、双语文档、响应 SLA——是维持 Active 阶段的预防性义务。

## 2. 版本生命周期

### 2.1 SemVer 规则

遵循 SemVer 2.0 + Conventional Commits 自动算版（`feat:` → minor、`fix:` → patch、`BREAKING CHANGE:` → major），由 commitlint + semantic-release 在 CI 强制执行：

- **0.x 阶段（Incubating）**：公共 API（REST 端点、`CleanedArticle` Schema、源元数据模型）允许 breaking，但次版本号变更必须在发布说明中给出迁移指引。
- **1.x 起（Active）**：major 变更须先在 issue 中公示 ≥14 天并附迁移期承诺；27 个 REST 端点的 OpenAPI 契约是兼容性的裁决基准。
- 版本号、CHANGELOG、tag 由 semantic-release 自动生成；发布说明的人工摘要与迁移指引由 Release Manager 补写，不得省略。

### 2.2 分支模型

monorepo（`apps/` 前端 Next.js、`apps/api` 后端 FastAPI、`packages/` 共享包），采用「主干开发 + 短期分支」的轻量模型：

| 分支 | 用途 | 保护规则 |
|---|---|---|
| `main` | 唯一主干，始终可发布；发布只从 main 触发 | Branch protection：禁止 force push 与删除；PR 必需 ≥1 名 CODEOWNER approve（自身 PR 除外）；required status checks 全绿（见 `05` 文档 CI 清单）；要求 linear history（squash 或 rebase 合并） |
| `feature/*`、`fix/*` | 个人工作分支，存活 ≤2 周 | 无保护，合入即删 |
| `release/x.y` | 发布前冻结分支，仅用于 rc 期修缺，存活 ≤1 周 | 仅 Release Manager 可推；cherry-pick 回 main |
| `hotfix/*` | 生产/安全紧急修复 | 从最新 tag 切出，修复后同时合入 main 并立即发 patch |

### 2.3 LTS 策略

- v1.x 期间**不设并行 LTS 线**：项目处于单团队单产品线阶段，多线维护成本不可承受；最新 minor 即支持线，安全修复只回溯到上一个 minor。
- v2.0 发布后，v1 最后 minor 自动转入 12 个月安全维护期（仅 patch），与 v2 并行。
- 每个 major 的维护终止日期写入 `SECURITY.md` 的支持矩阵，到期前 90 天公告。

## 3. 里程碑生命周期（M0–M4）

里程碑是 v1.0 交付期（基准 15 周）的顶级进度单元，每个里程碑按「入口条件 → 活动 → 出口门禁」三段管理。出口门禁的完整量化验收标准见 `03-acceptance-criteria.md`，此处定义流转规则；延期处置见 `02-progress-control.md`。

| 里程碑 | 周期 | 入口条件 | 主要活动 | 出口门禁（摘要） |
|---|---|---|---|---|
| M0 筹备 | W1–W2 | 核心团队到位；设计文档 v1.0 定稿 | monorepo、治理文件（LICENSE/NOTICE/CONTRIBUTING/行为准则/SECURITY.md）、CI（测试+commitlint+许可证扫描）、semantic-release、docker-compose 骨架（12 服务） | CI 全绿；12 服务健康检查 100%；许可证扫描零 GPL/AGPL 传递依赖；amd64/arm64 镜像构建成功 |
| M1 抓取内核 | W3–W6 | M0 门禁签字通过；种子源策展（M0 即启动）累计 ≥300 家 | 源注册表+15 张核心表、Mercator 前后队调度、四级发现降级、条件 GET/自适应轮询、第一级规则清洗、对象存储存档 | 300 源入库连续抓取 72h；抽取成功率 ≥90%；单篇清洗 <50ms；重复摄入率下降 ≥90%；礼貌违规 0 次 |
| M2 地域代理+LLM 管线 | W7–W10 | M1 门禁签字通过；≥50 个已知受限源样本集就绪 | 受限信号判定、三级代理绑定、受限降级与提示 API、渲染 worker 小池、第二级 LLM 清洗、embedding 事件聚类、第三级整理 | 受限判定准确率 ≥95% 且 429/5xx 误判 0 次；质量门 1 判负率 ≤20%；事件归并 precision ≥85%；渲染占比 ≤15%；LLM 月成本 ≤$100 |
| M3 全栈应用 | W8–W12（与 M2 后半并行） | API 接口契约（OpenAPI）冻结；M1 门禁通过 | Next.js 三界面、Better Auth 注册三态、Casbin RBAC、27 端点、SSE、健康度看板 | 27/27 契约测试通过；状态机 6 条迁移路径全覆盖；关键路径 E2E 全通过；限流分层实测生效 |
| M4 加固发布 | W13–W15 | M2、M3 门禁均签字通过；源清单 ≥1,000 家候选就绪 | 扩源至 1,000 家（150+ 国家）、72h 压测与故障注入、安全自查、三套文档站、v1.0.0 tag | 1,000 源在线且全库抽取成功率 ≥90%、断更源 <5%；72h 零 P0；许可证与密钥扫描零高危；30 分钟全新部署验证 |

里程碑流转规则：

1. **入口检查**：不满足入口条件不得开工，由 Release Manager 在迭代计划会上确认。
2. **出口评审**：Gate Review 会议按 `03-acceptance-criteria.md` 逐条裁决，产出签字记录（模板见 `03` 文档附录）；任一「必须通过项」未达即不通过，禁止「先过门后补票」。
3. **不可顺延项**（设计文档 7.3.1）：三个质量门与地域受限机制在任何裁剪中不可顺延；可顺延项仅限 WebSub 推送、每日离线 HDBSCAN 校正、多架构镜像以外的发布工程（顺延至 v1.1）。
4. **并行边界**：M3 与 M2 的并行以「OpenAPI 契约冻结」为唯一前置，契约变更走 `04-code-quality.md` 的 breaking change 流程。

## 4. 安全响应生命周期

安全治理文件为仓库根 `SECURITY.md`（M0 交付物），本节定义其执行生命周期：

1. **接收**：漏洞仅经 GitHub Private Vulnerability Reporting 或 SECURITY.md 公布的加密邮箱提交；公开 issue 报安全问题的，立即转为私有并提醒报告人。
2. **响应 SLA**：首次响应 ≤72 小时；严重级（远程未授权访问、密钥/凭据泄漏、供应链投毒）7 天内出修复或缓解措施；中低级并入下一迭代，最长 30 天。
3. **披露流程**：私下确认 → 修复 + patch 发布 → GitHub Security Advisory 公开（默认协调披露期 90 天，报告人同意可缩短）→ CHANGELOG 与发布说明同步。
4. **本项目特有风险面**：代理凭据（仅存本地密钥库引用、不落明文）、API Key（哈希存储）、审计日志、抓取管线的注入面、LLM 输出的确定性检查绕过——安全自查（M4）与每次 PR 的安全维评审（见 `04` 文档 rubric）须覆盖上述清单。
5. **依赖漏洞**：Dependabot/Renovate 每日扫描，高危 ≤7 天修复；许可证性质的安全事件（如依赖转 GPL/AGPL，先例：Zitadel 2025-03 转 AGPL-3.0）按同等 SLA 处置——锁定旧版本或替换组件。

安全事件复盘：每个严重级事件在修复后 14 天内完成无指责复盘（blameless postmortem），结论若为 harness 规则缺口，提交 harness 文档修订 PR。
