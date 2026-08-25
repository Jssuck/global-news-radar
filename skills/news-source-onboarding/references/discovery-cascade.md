# 发现策略四级降级链

按 1→4 顺序探测，取最高可用层级填入 `discovery.strategy`。每级给出「可用判定」与「不可用信号」。

## 第 1 级：RSS/Atom（最优）

- 探测路径：`/`、`/feed`、`/rss`、`/rss.xml`、`/feed.xml`、`/atom.xml`、`/index.xml`，以及首页 HTML 中 `<link rel="alternate" type="application/rss+xml|application/atom+xml">`
- 可用判定：HTTP 200 且为合法 feed（feedparser 可解析、`entries > 0`）、最新条目在 72 小时内
- 加分项：响应含 `Link: <...hub...> rel="hub"` → 记录 `websub_hub`，运行时走推送而非轮询
- 不可用信号：404、返回 HTML、最新条目超过 72 小时（feed 已荒废）

## 第 2 级：sitemap

- 探测路径：robots.txt 的 `Sitemap:` 指令、`/sitemap.xml`、`/sitemap_index.xml`、`/news-sitemap.xml`（Google News sitemap 优先）
- 可用判定：可解析、URL 条目含 `<lastmod>`、近 7 天有新 URL
- 注意：`<lastmod>` 只作提示不作触发器——CMS 重部署会批量刷新时间戳，直接信任会造成无效重抓；运行时必须与内容哈希/条件 GET 组合

## 第 3 级：HTML 列表页

- 仅在前两级失败时启用。填写 `list_url`（首页或频道页）与 `link_selector`（文章链接 CSS 选择器）
- 可用判定：选择器稳定命中 ≥10 个文章链接，链接为绝对 URL 或可拼接
- 风险：改版即失效，入库时必须在 `legal.notes` 标注「HTML 解析脆弱，需黄金样本回归」

## 第 4 级：聚合层兜底

- GDELT DOC 2.0 API 按 `domain:` 过滤，或 Google News RSS 按站点过滤
- 仅获得标题+URL 级元数据，正文仍需抓原站；标记 `discovery.degraded: true`
- 该级也意味着「发现链已断」，应在源健康看板中降级展示并提示社区修复

## 常见失败模式

- 头部媒体持续撤 feed（2025-2026 趋势）：主流媒体 RSS 覆盖率仅约三成，接入时直接两级并探可节省时间
- feed 存在但需登录/付费墙：不算可用 RSS，降级处理并在 `legal.notes` 标注付费墙
- 多语种站：每个语种版本若路径独立（/zh/、/es/），按独立源分别接入，共享 `base_url` 但独立 source 文件
