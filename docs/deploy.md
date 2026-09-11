# 部署指南

## 快速部署（推荐）

```bash
git clone https://github.com/Jssuck/global-news-radar.git
cd global-news-radar
docker compose up -d
# → http://localhost:8000（新闻流 / 源看板 / 审核台 / API 文档 /docs）
```

预期 30 分钟内完成（M4-F4 演练口径）。SQLite 数据在 `gnr-data` 卷中持久化。

## 本地开发

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

## 多用户模式

默认 `GNR_AUTH_REQUIRED=0`（自托管免登录，写操作放行）。对多用户部署：

```env
GNR_AUTH_REQUIRED=1
GNR_REGISTRATION_MODE=approval   # open | approval | invite
```

首个注册用户自动成为 `admin`（引导路径），之后在「审核台 → 注册审核」审批他人，或生成邀请码实现旁路直通。

## 可选能力

| 能力 | 配置 | 未配置时行为 |
|---|---|---|
| LLM 清洗/整理 | `GNR_LLM_BASE_URL` + `GNR_LLM_MODEL` | 该级关闭，仅规则清洗 |
| 事件聚类 | `GNR_EMBED_BASE_URL` | 聚类关闭 |
| 渲染兜底 | `GNR_RENDER_ENABLED=1` + playwright | anti_bot 源不重试 |
| 地域受限 | `config/proxies.yaml`（BYO 代理） | 走 GDELT 聚合层降级 |

凭据一律走环境变量名引用（`credentials_env` / `GNR_LLM_API_KEY_ENV`），不落库不落库——见 [docs/operations.md](operations.md) 安全小节。

## 健康检查

`GET /api/v1/health` 返回 DB/最近抓取/待处理提示/死信/GDELT 对照/各 LLM 级配置状态。
