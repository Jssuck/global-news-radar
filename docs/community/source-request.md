# 提交/请求新媒体源（社区指南）

## 添加新源

1. Fork 仓库，在 `sources/` 新增一个 YAML 文件（文件名 = 域名，点号转连字符，如 `bbc-news.yaml`）。
2. 填写下列模板字段；拿不准的发现策略先填 `rss`。
3. 运行 `python scripts/validate_sources.py` 与 `python scripts/verify_feed.py --source-key <key>` 自测。
4. 提 PR。维护者复测通过后合入；持续失败的源按 `triage` 块标记而非删除。

## 源模板

```yaml
source_key: example-news        # 域名点号转连字符
name: Example News              # 显示名
base_url: https://example.com
country: gb                     # ISO-3166 alpha-2 小写
language: en
media_type: newspaper           # newspaper|tv|agency|online|magazine…
influence_tier: national        # global|national|regional
feed_url: https://example.com/rss.xml
discovery_strategy: rss         # rss|sitemap（html_list 待实现）
sitemap_url: null               # strategy=sitemap 时必填
active: true
# 分诊记录（验证/复测后由脚本回写，人工勿填）：
# triage:
#   verdict: active|geo_suspected|geo_restricted|anti_bot|blocked_legal|dead|inconclusive
#   evidence: HTTP 200 valid feed
#   date: 2026-09-11
```

## 合规红线

- 只收**公开** RSS/sitemap/列表页；需登录、付费墙、robots 禁止的一律标 `blocked_legal` 停用
- 不分发全文：源描述与示例只放标题/链接/发布时间
- 地域受限源不配置代理绕过指引之外的「技术手段」；由部署方按 `config/proxies.example.yaml` 自决并自担合规责任

## 请求新源（不写 YAML）

开 Issue 标注 `source-request`，附：媒体名、官网、国家、语种、RSS/sitemap 地址（若知）。
