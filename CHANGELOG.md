# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- M2a 地域受限与代理：proxy_profiles/proxy_bindings/geo_hints 三表；源级>国家级>全局绑定解析注入抓取出口（凭据仅环境变量引用）；geo 中信号（地域警告页重定向、正文截断骤降≥90%）经他国出口对照确认；geo_restricted 无代理时走 GDELT 聚合层降级（articles.degraded=1，仅标题+URL）；REST：/geo-hints、/proxy-profiles、/proxy-bindings
- M2b 第二级 LLM 清洗：ChatProvider 协议 + OpenAI 兼容实现（GNR_LLM_BASE_URL 未配置则跳过）；CleanedArticle Pydantic 闸门 + 8-词 shingle 重叠/未来时间戳确定性检查；纠错重试一次后进 dead_letters；llm_calls 记 token 成本；is_ad_or_boilerplate 丢弃分支；质量门1判负率（articles.gate1_failed）
- M2c 事件聚类与渲染兜底：Embedder 协议（OpenAI 兼容 /embeddings）+ float32 blob 存储；两遍 KNN+UnionFind（篇级 ≥0.78 并集、质心 ≥0.85 合并）；OrganizedEvent 闸门（成员<2 不送 LLM，失败保留 organized=0 占位）；Playwright 渲染兜底仅 anti_bot 源且受日预算限制；后台 organize_loop
- M3a 认证与审核：users/sessions/api_keys/invite_codes 表；PBKDF2 密码散列、凭证只存 SHA-256；注册 pending→approved|rejected 三态状态机（首位用户直升 admin 引导）；RBAC admin/editor/viewer；写端点权限门（GNR_AUTH_REQUIRED）；/api/v1/* 令牌桶限流
- M3b REST 补全：GET/POST/PATCH /sources、/sources/{id} 详情、/events 列表与成员、/dead-letters 队列、/health 健康度、/stream SSE 事件流（fetch/geo_hint/event 三通道）
- M3c 前端界面：新闻流事件区与降级/LLM 徽标、源管理表单与启停、/admin 审核台（注册审批/邀请码/geo-hints/代理配置/死信）、登录与注册页

### Changed
- 源健康复测（出口恢复）：443 源 93.9% 可达；bolnews/kompas/tribune 降级至 sitemap 策略；abs-cbn/lebanonfiles/mindanews/thewhistler 按 robots 不可读保守规则记 blocked_legal；thejakartapost/nation-africa 记 inconclusive 待 html_list
- M1 门禁报告补附录 A 记录复测与处置

## [0.1.0] - 2026-09-11

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
