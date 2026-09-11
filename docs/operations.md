# 运维手册

## 日常巡检

- `/sources` 源看板：geo 状态徽标、最近抓取时间、绑定代理、逐源启停
- `/api/v1/health`：DB 可写、最近抓取、open geo_hints、死信、GDELT 今日对照
- `/api/v1/dead_letters`：清洗/整理失败项，人工复核后 resolve
- `/api/v1/gdelt-checks`：受限源每日 GDELT 命中 vs 本地收录（M3-F4 漏抓对照）
- `/admin` 审核台：注册审批、邀请码、受限提示、代理配置、死信、审计日志

## 降级路径

| 故障 | 行为 |
|---|---|
| LLM 不可达 | 清洗/整理调用重试一次后进 `dead_letters`；事件保留 organized=0 占位 |
| Embedding 不可达 | 聚类循环跳过该轮，下轮重试 |
| 单源抓取异常 | 仅记日志，不影响同批其余源（worker 级隔离） |
| 源被地域封锁且无代理 | GDELT 聚合层降级：`degraded=1` 仅标题+URL |
| anti_bot 源 | 中信号保留；开渲染开关且预算内时走渲染兜底 |

## 安全基线

- 密码 PBKDF2-HMAC-SHA256（120k rounds）；session/API key 只存 SHA-256 摘要
- 封禁（suspended）即时吊销 session 与 API key；rejected 为终态
- 限流两档：匿名/session 100 req/min，API Key 600 req/min（429 + Retry-After）
- 写端点权限门经 `guard()`，敏感操作（审批/角色/封禁）落 `audit_logs`
- 代理凭据仅环境变量引用；密钥不入库不入库不入库
- robots.txt 遵守并缓存 1h；公开 API 不分发全文（仅标题/摘要/链接）

## 备份

SQLite 单文件：`cp data/gnr.db backups/gnr-$(date +%F).db`（写锁已串行化，运行中可复制）。
