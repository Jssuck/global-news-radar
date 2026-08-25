# 代码质量评判标准

> **目的**：定义每一行合入 main 的代码必须满足的质量约束——语言规范、测试门槛、评审标准、CI 门禁与禁止事项，使质量裁决不依赖评审者个人口味。
> **适用范围**：monorepo 全部代码（Python 后端/爬虫/管线 + TypeScript 前端）、测试 fixtures、基础设施配置（Dockerfile、compose、CI workflow）。
> **关联文档**：流程级 DoD 见 `03-acceptance-criteria.md` §6；CI 工作流实现见 `05-harness-enforcement.md`；许可证策略依据见设计文档第 6、8 章。

## 1. 双语言规范

### 1.1 Python（FastAPI / 爬虫 / 清洗管线）

| 项 | 标准 | 工具与配置 |
|---|---|---|
| 代码风格 | ruff format + ruff lint（启用 `E,F,I,UP,B,SIM,RUF` 规则集），行宽 100 | `ruff`，配置入 `pyproject.toml`，CI 零告警 |
| 类型检查 | **mypy strict**：`disallow_untyped_defs`、`warn_return_any` 等全开；第三方无桩库以显式 `type: ignore` + 注释说明豁免 | `mypy --strict`，CI 必须通过 |
| 数据校验 | 所有 API 边界与 LLM 输出一律 Pydantic 模型/JSON Schema 校验，禁止裸 dict 穿越层间 | 评审人工检查 |
| 依赖约束 | trafilatura 锁定 **≥1.8.0**（1.8.0 起 Apache-2.0）；新增依赖须声明许可证并过扫描 | `uv`/pip-tools 锁定文件入仓 |
| 安全基线 | bandit 扫描；密钥检测；凭据只存密钥库引用不落明文 | CI 必须通过（高危），中危可警告 |

### 1.2 TypeScript（Next.js 前端 / 共享包）

| 项 | 标准 | 工具与配置 |
|---|---|---|
| 代码风格 | eslint（`next/core-web-vitals` + `typescript-eslint` recommended）+ prettier，CI 零告警 | `eslint`/`prettier` |
| 类型检查 | **tsc strict**：`strict: true`、`noUncheckedIndexedAccess: true`；禁止新增 `any`（eslint `@typescript-eslint/no-explicit-any` 为 error） | `tsc --noEmit` |
| 前后端契约 | API 类型由 OpenAPI schema 生成，禁止手抄接口类型 | openapi-typescript 生成物 |
| 组件规范 | Tailwind + shadcn/ui 源码拷贝组件归 `components/ui/`，改动须在 PR 描述中注明 | 目录约定 |

## 2. 测试金字塔与覆盖率门槛

### 2.1 测试金字塔

| 层 | 占比目标 | 内容 | 工具 |
|---|---|---|---|
| 单元测试 | ~70% | 纯函数/模块：抽取兜底链、去重三段论、元数据证据链裁决、绑定优先级解析、状态机迁移 | pytest（Python）、vitest（TS） |
| 集成测试 | ~25% | Celery 任务链、DLQ 路由、限流分层、DB 迁移、契约测试（27 端点 OpenAPI） | pytest + testcontainers、schemathesis |
| E2E | ~5% | 关键路径：注册→审批→配置源→查看事件流；受限提示→配置代理→恢复 | Playwright |

### 2.2 覆盖率门槛（CI 强制）

| 范围 | 门槛 | 说明 |
|---|---|---|
| 核心管线（清洗/去重/调度/受限检测，`packages/pipeline` 与对应后端模块） | **行覆盖 ≥80%** | 全系统质量与成本的决定层，门槛最高 |
| 仓库整体 | **行覆盖 ≥70%** | PR 增量覆盖率同样 ≥70%，防止「存量达标、增量裸奔」 |
| 爬虫解析器回归 | **黄金样本集全量通过且精度不回退** | 200 篇人工标注样本（M1 基线）+ ≥50 受限源样本（M2 基线），fixtures 不得含真实新闻全文（用合成/授权样本），每次解析器改动跑全量回归，成功率/判定准确率低于基线即 FAIL |

## 3. PR 评审 Rubric（五维度清单）

评审者按以下清单逐项给出「通过/意见/阻断」；任一**阻断项**未解决不得 approve：

| 维度 | 检查项 | 典型阻断项 |
|---|---|---|
| 正确性 | 逻辑符合设计文档模块边界；失败路径显式路由（不静默丢失）；幂等性（任务可重放）；并发与重试安全 | 吞异常、静默降级、破坏「一切结果可重算」原则 |
| 可读性 | 命名与结构自解释；公开函数有 docstring/注释；改动聚焦单一目的 | PR 混入无关重构；魔法数无出处 |
| 测试 | 新增逻辑有对应测试；覆盖率达标；解析器改动过黄金样本回归 | 无测试的管线改动；注释掉失败测试 |
| 安全 | 输入边界校验；凭据/密钥不落明文不落日志；注入面（SQL/命令/prompt）有防护；RBAC 端点权限标注正确 | 明文凭据；越权可达的管理端点；LLM 输出绕过确定性检查直接入库 |
| 许可证合规 | 新增依赖许可证为 MIT/Apache-2.0/BSD/ISC/PostgreSQL License 宽松档；GPL/AGPL 组件仅独立服务网络调用；NOTICE 同步 | 任何 GPL/AGPL 代码级依赖（含传递）；复制 RSSHub/Firecrawl 源码 |

合并规则：≥1 名 CODEOWNER approve；作者不得 approve 自己的 PR；大面积改动（>800 行 diff）须拆分或经 Maintainer 会签。

## 4. 静态检查与 CI 门禁表

| 检查项 | 级别 | 工具 | 说明 |
|---|---|---|---|
| ruff / eslint+prettier | 必须通过 | ruff、eslint | 零告警 |
| mypy --strict / tsc strict | 必须通过 | mypy、tsc | 零错误 |
| 单元+集成测试 + 覆盖率门槛 | 必须通过 | pytest、vitest、coverage | §2.2 门槛 |
| 黄金样本回归 | 必须通过 | 自定义回归任务 | 解析器相关 PR 触发 |
| 契约测试 | 必须通过 | schemathesis | API 改动触发 |
| commitlint（Conventional Commits） | 必须通过 | commitlint + husky | PR 标题与提交 |
| 许可证扫描 | 必须通过 | scancode/FOSSA 类 | GPL/AGPL 代码级依赖零容忍 |
| 密钥泄漏扫描 | 必须通过 | gitleaks/trufflehog | 高危零容忍 |
| bandit（Python SAST） | 高危必须通过、中危警告 | bandit | 中危警告须在 7 天内处置 |
| 依赖漏洞扫描 | 高危必须通过、其余警告 | Dependabot/pip-audit/audit-ci | 高危 ≤7 天修复（安全 SLA） |
| Docker 镜像构建（amd64/arm64） | 必须通过 | docker buildx | M0 起 |
| Lighthouse 性能预算 | 警告项 | Lighthouse CI | <80 告警不阻断（M3 验收时转必须） |

## 5. 提交规范：Conventional Commits

格式 `type(scope): subject`，类型与发版联动（semantic-release）：

| type | 语义 | 版本影响 |
|---|---|---|
| `feat` | 新功能 | minor |
| `fix` | 缺陷修复 | patch |
| `perf` / `refactor` / `docs` / `test` / `chore` / `ci` | 性能/重构/文档/测试/杂务/CI | 不发版（perf 可 minor） |
| 任意 + `BREAKING CHANGE:` 脚注 | 破坏性变更 | major，须附迁移指引 |

scope 建议：`sources`、`scheduler`、`fetcher`、`pipeline`、`llm`、`geo`、`proxy`、`api`、`web`、`auth`、`infra`。PR 标题即合并后的提交信息（squash merge），同样过 commitlint。

## 6. 禁止事项（红线，违反即 revert + 复盘）

1. **不引入 GPL/AGPL 代码级依赖**——含传递依赖；trafilatura 必须 ≥1.8.0；RSSHub（AGPL）、Firecrawl 核心（AGPL）、Typesense 服务器（GPL-3.0）、Zitadel（AGPL-3.0）等只可独立服务网络调用或阅读源码参考，一行代码不进本仓库。
2. **不提交密钥与凭据**——任何 API Key、代理凭据、会话 cookie 不入仓、不落明文日志；`.env.example` 只含占位符。
3. **不静默绕过 robots.txt**——默认遵守 robots.txt（RFC 9309）与 Crawl-delay；per-host 限速与 429 退避不得关闭；任何「绕过/规避」性质的行为变更（含 UA 伪装成浏览器以规避封禁）禁止合入；ToS 禁抓源保持打标默认禁用。
4. **不内置版权样本与技术保护措施绕过**——fixtures 不含真实新闻全文/图片；不实现付费墙绕过、验证码破解、登录态伪造。
5. **不静默失败**——管线任何失败必须显式路由（重试/DLQ/告警/降级标志），禁止空 `except: pass`。
6. **不绕过门禁**——禁止 force push main、禁止以 `--no-verify` 跳过 hooks、禁止管理员特权绕过 required status checks 合并（含 Maintainer 自身）。
