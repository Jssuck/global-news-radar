# Contributing to Global News Radar

感谢贡献！本项目按 [docs/harness/](docs/harness/) 的工程约束运作，请先花 10 分钟阅读 [00 总览](docs/harness/00-overview.md) 与 [04 代码质量](docs/harness/04-code-quality.md)。

## 最简单的首次贡献：添加一个媒体源

1. 复制任一 `sources/*.yaml`（M1 后）或按 `skills/news-source-onboarding/references/source-schema.md` 新建一源一文件
2. 按 `skills/news-source-onboarding/SKILL.md` 五步流程完成探测与自检
3. 运行 `python3 skills/news-source-onboarding/scripts/validate_source.py <your-source.yaml>`，全绿后提 PR
4. 成为该源的维护者（源维护者责任制）：源故障时 @你，14 天无响应则重新分配

## 代码贡献流程

1. Fork & 从 `develop` 切分支：`feat/...`、`fix/...`、`source/...`、`docs/...`
2. 提交遵循 Conventional Commits（`feat: add sitemap discovery for chosun.com`）
3. PR 必须通过 CI 门禁：lint（ruff / eslint）、type check（mypy / tsc strict）、测试（核心管线覆盖率 ≥80%）、许可证扫描
4. PR 评审按五维 rubric：正确性 / 可读性 / 测试 / 安全 / 许可证合规
5. 合并使用 squash merge，保持 `develop` 线性历史

## 硬性红线（PR 会被直接拒绝）

- 引入 GPL/AGPL 许可证的代码依赖（宽限：独立部署的网络服务）
- 提交任何密钥、Token、代理凭据
- 绕过 robots.txt、付费墙或地域技术措施的代码
- 存储/分发新闻全文的功能（仅标题、极短摘要、链接）

## Issue 规则

- 任务认领后 14 天无活动自动释放（见 02-progress-control.md）
- Bug 报告请附：源定义、失败响应样本、期望 vs 实际
- 新源请求使用「Source Request」模板

## 行为准则

见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。安全问题请走 [SECURITY.md](SECURITY.md) 的私密渠道，不开公开 issue。
