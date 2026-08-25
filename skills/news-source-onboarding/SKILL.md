---
name: news-source-onboarding
description: >
  Global News Radar 项目的媒体源接入标准流程。当需要为项目新增、审核或修复一个新闻媒体抓取源时使用：
  包括填写源元数据（国家/语种/类型/影响力分级）、按四级降级链探测发现策略（RSS → sitemap → HTML 列表页 → 聚合层）、
  地域受限初检、注册入库与接入验收。触发场景：「添加一个媒体源」「这个源抓不到/解析失败」「审核社区提交的 source 定义」
  「为 xx 国家补充主流媒体源」。
---

# 媒体源接入（News Source Onboarding）

## 概述

将一个新闻媒体注册为可监控源，必须走完整五步流程并达到验收标准，禁止只填一个 URL 就入库。所有源定义以 YAML 文件形式存放（一源一文件），入库前必须通过 `scripts/validate_source.py` 校验。

## 五步流程

### 1. 采集元数据

按 `references/source-schema.md` 的字段表填写源定义。必填字段缺失即拒绝入库。关键字段：`country`（ISO 3166-1 alpha-2）、`language`（BCP-47）、`media_type`（agency/newspaper/tv/radio/online）、`influence_tier`（national/major/regional）、`base_url`、`legal.robots_checked`。

### 2. 探测发现策略（四级降级）

按 `references/discovery-cascade.md` 的顺序逐级探测，记录最终生效层级到 `discovery.strategy`：

1. **RSS/Atom**：检查常见路径（/feed、/rss、/rss.xml、/feed.xml）与首页 `<link rel="alternate">`；支持 WebSub 的填写 `discovery.websub_hub`
2. **sitemap**：解析 robots.txt 的 Sitemap 指令或 /sitemap.xml，确认含 `<lastmod>` 且近 7 天有更新
3. **HTML 列表页**：仅在无 feed/sitemap 时启用，需给出列表页 URL 与文章链接 CSS 选择器
4. **聚合层兜底**：GDELT/Google News 按域名过滤，标记 `discovery.degraded: true`

### 3. 地域受限初检

从与目标国家不同的网络出口请求首页与一个文章页：返回 451、403 且含地域封锁语料、或重定向到地域警告页 → 设置 `geo.status: geo_restricted` 并填写 `geo.required_region`，后续处置转交 **geo-block-triage** skill。初检通过则 `geo.status: ok`。

### 4. 校验

```bash
python3 scripts/validate_source.py <source.yaml>
```

校验全部通过（exit 0）才可提交 PR；`--json` 输出机器可读结果供 CI 调用。

### 5. 接入验收（全部满足才算完成）

- 发现策略实测可拿到近 24 小时新文章 URL（三级及以上策略需附实测样例链接 ≥3 条）
- 正文抽取试跑 ≥5 篇，trafilatura 成功率 ≥80%，正文非空且语言与 `language` 字段一致
- 近 14 天更新频率画像已记录（`update_profile.estimated_interval`），用于调度间隔初始化
- 无版权风险：`legal.robots_checked: true` 且目标路径未被 robots.txt 禁止

## 质量红线

- 不添加 UGC/社交媒体/内容农场源
- 不为「能抓到」而绕过 robots.txt 或地域技术措施——受限就标记，提示用户配置代理，不静默绕过
- 一源一文件，文件名 = 规范化域名（如 `bbc-com.yaml`）
