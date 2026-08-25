# 源定义 Schema（source.yaml）

## 字段表

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 媒体官方名称（原文，如 "BBC News"） |
| `base_url` | string(url) | 是 | 主站 URL，https 优先 |
| `country` | string | 是 | ISO 3166-1 alpha-2（如 `gb`），总部所在国 |
| `language` | string | 是 | BCP-47 主语言（如 `en`、`zh-CN`） |
| `languages_extra` | list | 否 | 其他语种版本 |
| `media_type` | enum | 是 | `agency` / `newspaper` / `tv` / `radio` / `online` |
| `influence_tier` | enum | 是 | `national`（全国性主流）/ `major`（大类/大都会）/ `regional` |
| `discovery.strategy` | enum | 是 | `rss` / `sitemap` / `html_list` / `aggregator`，按四级降级探测结果填最高可用级 |
| `discovery.feed_url` | string | 条件 | strategy=rss 时必填 |
| `discovery.websub_hub` | string | 否 | 支持 WebSub 时填 hub URL |
| `discovery.sitemap_url` | string | 条件 | strategy=sitemap 时必填 |
| `discovery.list_url` / `discovery.link_selector` | string | 条件 | strategy=html_list 时必填（列表页 URL + 文章链接 CSS 选择器） |
| `discovery.degraded` | bool | 否 | strategy=aggregator 时为 true |
| `geo.status` | enum | 是 | `ok` / `geo_restricted` / `unknown` |
| `geo.required_region` | string | 条件 | geo_restricted 时必填，ISO 国家码 |
| `geo.evidence` | string | 条件 | 判定依据（状态码、语料样例、出口对照），geo_restricted 时必填 |
| `update_profile.estimated_interval` | string | 是 | 近 14 天估算更新间隔，ISO 8601 duration（如 `PT10M`） |
| `legal.robots_checked` | bool | 是 | 必须 true 才可入库 |
| `legal.notes` | string | 否 | ToS 特别条款备注 |
| `maintainers` | list | 否 | 源维护者 GitHub ID，社区共建责任到人 |

## 示例

```yaml
name: BBC News
base_url: https://www.bbc.com
country: gb
language: en
media_type: tv
influence_tier: national
discovery:
  strategy: rss
  feed_url: https://feeds.bbci.co.uk/news/rss.xml
geo:
  status: ok
update_profile:
  estimated_interval: PT5M
legal:
  robots_checked: true
maintainers: []
```
