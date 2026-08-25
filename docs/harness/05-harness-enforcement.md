# Harness 框架的执行与工具化

> **目的**：把 00–04 四份文档的约束落地为可执行的 CI/CD 工作流、自动化门禁脚本与框架自身的修订机制，保证 harness 不停留在纸面。
> **适用范围**：GitHub 仓库的 Actions 工作流、分支保护配置、`scripts/` 工具、以及 harness 文档自身的变更管理。
> **关联文档**：门禁指标来源 `03-acceptance-criteria.md`；检查项清单 `04-code-quality.md` §4；KPI 定义 `02-progress-control.md` §2。

## 1. CI/CD 工作流清单（GitHub Actions）

工作流文件统一置于 `.github/workflows/`，命名与职责一一对应：

| 工作流 | 触发 | 内容 | 对应约束 |
|---|---|---|---|
| `lint.yml` | PR / push to main | ruff + mypy --strict；eslint + prettier + tsc --noEmit；commitlint | `04` §1、§5 |
| `test.yml` | PR / push to main | pytest（单元+集成，含 testcontainers）+ vitest；覆盖率上传并校验门槛（核心管线 ≥80%、整体与增量 ≥70%） | `04` §2 |
| `golden-regression.yml` | PR（解析器/管线路径变更时）| 黄金样本集回归：200 篇抽取样本成功率、≥50 受限源判定准确率，低于基线即 FAIL | `04` §2.2 |
| `contract.yml` | PR（API 路径变更）/ nightly | schemathesis 对 27 端点跑 OpenAPI 契约测试 | `03` M3-F1 |
| `build.yml` | PR / push to main / tag | docker buildx 构建 amd64/arm64 双架构镜像；tag 推送时发布 ghcr.io | `03` M0-F4 |
| `license-scan.yml` | PR / nightly | scancode 全量依赖扫描，GPL/AGPL 代码级依赖即 FAIL；NOTICE 差异检查 | `04` §6 |
| `security.yml` | PR / nightly | gitleaks 密钥扫描、bandit、pip-audit/npm audit；高危 FAIL | `04` §4 |
| `e2e.yml` | push to main / nightly | Playwright 关键路径 E2E（注册→审批→配置源→事件流） | `03` M3-F3 |
| `gate-check.yml` | workflow_dispatch（里程碑出口）/ 每周定时 | 运行 `scripts/gate_check.py`，产出 PASS/FAIL 报告 artifact 并评论到指定 issue | 本文 §2 |
| `kpi-snapshot.yml` | 每周定时 | 采集 KPI 表快照（燃尽、源覆盖率、成功率、判负率、CI 通过率、issue 积压）写入迭代报告模板 | `02` §2 |
| `stale-claims.yml` | 每日定时 | 任务认领停滞回收：assigned 14 天无活动 → 提醒；72h 无响应 → unassign + `recovered` 标签 | `02` §4 |
| `release.yml` | push to main | semantic-release 算版、CHANGELOG、tag、发布说明草稿 | `01` §2 |

## 2. 质量门禁自动化：`scripts/gate_check.py`

约定如下，实现细节可演进，但接口契约不得破坏：

1. **单一事实源**：脚本读取 `03-acceptance-criteria.md` 中的量化指标表（表格须保持机器可解析格式：标准编号、阈值、测量方式三要素齐备），禁止在脚本内硬编码第二份阈值副本——指标变更只改 `03` 文档。
2. **调用方式**：`python scripts/gate_check.py --milestone M2 --window 7d --out report.md`；Gate Review 前 48h 由 Release Manager 执行，输出随签字记录归档。
3. **数据源**：健康统计表（PostgreSQL）、管线指标端点、成本监控报表、CI API、扫描报告 artifact；连不上数据源的条目标记 `MANUAL`，绝不默认 PASS。
4. **输出格式**：逐条 `标准编号 | 实测值 | 阈值 | PASS/FAIL/MANUAL | 证据链接`，末尾汇总计数；退出码 0=全部必须项 PASS，非 0=存在 FAIL（供 `gate-check.yml` 直接把结果反映到 required status check）。
5. **禁止人工覆盖**：脚本结果不得手工编辑；对结果有异议时修订测量口径（走 harness PR）后重跑。

## 3. GitHub 平台配置建议

**Branch protection（main）**：Require a pull request before merging（required approvals = 1，dismiss stale reviews，require review from Code Owners）；Require status checks to pass——勾入 `lint`、`test`、`build`、`license-scan`、`security`（contract/golden-regression 按路径触发时不强制）；Require linear history；禁止 force push 与删除；**Do not allow bypassing the above settings**（含管理员）。

**CODEOWNERS**（`.github/CODEOWNERS`）示例骨架：

```text
*                                    @org/maintainers
/apps/api/                           @org/backend
/apps/web/                           @org/frontend
/packages/pipeline/                  @org/pipeline
/sources/adapters/                   @org/maintainers   # 源维护者按子目录追加
/docs/harness/                       @org/maintainers   # harness 文档必须 Maintainer 评审
/.github/workflows/                  @org/maintainers
```

**其他**：启用 Private Vulnerability Reporting 与 Dependabot alerts/updates；`release` environment 设 required reviewer（当值 Release Manager）；Projects 看板字段包含里程碑、迭代、认领日期（供停滞回收脚本读取）。

## 4. 文档即代码

harness 五份文档与代码同仓、同评审纪律：任何修改走 PR，CODEOWNERS 强制 `@org/maintainers` 评审；量化指标的修改 PR 描述中必须注明依据（设计文档章节或 Gate Review 决议编号）；CI 对 `docs/harness/` 变更自动在 PR 中提示「此为治理文档变更」。治理文件（LICENSE/NOTICE/CONTRIBUTING/SECURITY.md）适用同一规则。

## 5. 季度回顾与修订机制

每季度最后一个迭代评审会后固定召开 harness 回顾（≤60 min）：

1. **有效性审计**：本季度门禁拦截记录（拦截了什么、漏放了什么）、黄/红灯触发次数、KPI 快照趋势；出现「门禁形同虚设」（连续全绿但事故频出）或「门禁过度阻断」（告警疲劳、绕过行为）均为修订信号。
2. **修订产出**：修订 PR 须在 30 天内合入或明确驳回；重大变更（覆盖率门槛、里程碑指标、角色权限）须全体 Maintainer 表决。
3. **版本化**：harness 文档页脚记录修订版本号与日期；每次回顾后打 `harness-vX.Y` tag，使任一时刻的 Gate Review 记录可回溯到当时生效的约束版本。
4. **反腐化**：回顾同时检查本框架与设计文档的漂移——设计文档事实性结论更新时，harness 必须在下一迭代内完成同步修订。
