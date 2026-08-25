# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- M0 筹备完成：仓库结构、MIT LICENSE、NOTICE、治理文件（CONTRIBUTING / CODE_OF_CONDUCT / SECURITY）
- 项目总体设计文档 v1.0（docs/design/design-v1.0.md）
- Harness 框架文档 6 篇（docs/harness/）：生命周期、进度把控、验收标准、代码质量、执行工具化
- 项目级 skills 4 个（skills/）：news-source-onboarding、geo-block-triage、cleaning-pipeline-qc、milestone-gate-review，含 4 个可执行校验脚本
- README 中英双语版本
- M1 抓取内核：种子源注册表 443 份（60+ 国 40+ 语种），RSS→sitemap 两级发现、条件 GET、robots.txt 合规缓存、per-host 限速、自适应轮询间隔、trafilatura 抽取兜底链
- M1 验收工具链：`scripts/verify_feed.py`（批量 feed 体检）、`scripts/collect_metrics.py`（全量抓取 + metrics）、`scripts/golden_regression.py`（黄金样本回归）、`scripts/triage_sources.py`（失败源分诊回写 YAML）
- 源 schema 扩展：`active` 开关 + `triage` 块（verdict/evidence/date/revisit），validator 同步支持
- M1 门禁评审报告（docs/harness/gate-reports/m1-gate-report.md）：**PASS**——onboarded 440、抽取成功率 95.8%、分诊后发现成功率 91.1%

### Changed
- sources_loader 支持 `active` 字段回写（triage 停用/恢复随 YAML 生效）
- 37 个持续失败源按 geo-block-triage skill 分诊停用：22 个 blocked_legal（robots 禁止，合规红线）、15 个 anti_bot（JS 挑战，M2 渲染抓取恢复）
