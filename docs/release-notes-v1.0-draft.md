# v1.0.0 发布说明（草稿）

> 本文档在 M4 门禁测量完成后随 tag `v1.0.0` 定稿发布。当前为草稿。

## 这是什么

Global News Radar 是一个**自托管**的全球主流媒体监控平台：443 个种子源
（60+ 国、40+ 语种）持续轮询，RSS→sitemap 发现降级，规则清洗 +
可选 LLM 二级清洗 + embedding 事件聚类，地域受限源走 GDELT 聚合层
降级而非静默断更。单进程 + SQLite，一条 `docker compose up -d` 可用。

## 主要能力

- **发现**：RSS / sitemap / robots 发现 / GDELT 聚合层兜底四级级联
- **清洗**：一级规则（trafilatura 兜底链）→ 质量门 1 → 可选 LLM
  二级清洗（schema 闸门 + 确定性检查 + DLQ）→ 三级事件整理
- **聚类**：两遍 KNN + UnionFind，跨语种事件归并
- **地域受限**：强/中信号判定 + 他国出口对照确认；BYO 三级代理绑定；
  无代理时 GDELT 降级（仅标题+URL）
- **多用户**：session/API Key 双通道、注册三档模式（open/approval/invite）、
  六条状态机迁移、RBAC 四角色、审计日志、分级限流
- **接口**：37 个 REST 端点 + SSE 事件流 + 三个服务端渲染界面

## 合规红线（部署前必读）

- **不分发全文**：公开 API 与界面仅输出标题/短摘要/原文链接
- **遵守 robots.txt**：robots 禁止的源标记 blocked_legal 停用
- **不静默绕过地域限制**：系统只给出判定证据与提示；是否经代理访问由
  部署者自决并自担责任（见 `config/proxies.example.yaml`）
- **凭据零明文**：密码 PBKDF2、token/key 仅存 SHA-256 摘要、
  代理凭据仅环境变量名引用
- **付费墙/需登录内容一律不采**：标记 blocked_legal

## 迁移指引（0.1.x → 1.0.0）

SQLite 自动增量迁移（`init_db` 幂等）：

- `users.role` 值 `viewer` 自动迁移为 `user`
- 新增列：`users.reason`；新表：`audit_logs`、`gdelt_checks`
- 新环境变量（均可选）：`GNR_REGISTRATION_MODE`（默认 approval）、
  `GNR_RATE_LIMIT_API_KEY_PER_MIN`（默认 600）
- 若此前用 `GNR_RATE_LIMIT_PER_MIN` 未显式设置：默认由 120 改为 100

无破坏性 API 变更；`X-API-Key` / `Bearer` 两种 API Key 头均兼容。

## 已知限制

- MVP 为单进程 asyncio 轮询 + SQLite；分布式（Celery/Postgres/Valkey）
  为 v2 方向
- html_list 发现策略未实现（设计预留，inconclusive 源待补）
- M2-F4 事件归并 precision 人工抽检、M4-F1 千源/72h 窗口、
  M3-N1 Lighthouse/SSE 延迟实测为门禁遗留项
