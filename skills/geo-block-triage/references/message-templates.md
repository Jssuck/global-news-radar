# 用户提示文案模板

## 要素

每条提示必须包含：①受限地域（国家名，由 ISO 码转换）②证据（状态码与语料样例）③配置入口。禁止「绕过」「破解」字眼；统一表述「仅允许 xx 地区 IP 访问」。

## 源卡片提示（中文）

> ⚠ 该媒体仅允许 **{country_name}** 地区 IP 访问（{evidence}）。当前抓取已降级为仅标题模式。
> 如需完整抓取，请添加 {country_name} 地域的代理：[配置代理]

## 源卡片提示（English）

> ⚠ This source only allows access from IP addresses in **{country_name}** ({evidence}).
> Fetching is degraded to headline-only mode. To enable full fetching, add a proxy located in {country_name}: [Configure proxy]

## 证据行格式

- `HTTP 451` → `检测到 HTTP 451（法律要求不可用）`
- `403 + 语料` → `HTTP 403，响应包含 "{snippet}"`
- `重定向` → `首页重定向至地域警告页（{url}）`
- `截断` → `正文长度较近 30 天均值下降 {pct}%`

## API 响应（/api/v1/sources/{id} 片段）

```json
{
  "geo_status": "geo_restricted",
  "required_region": "kr",
  "hint": "该媒体仅允许 韩国 地区 IP 访问，需要添加 韩国 地域的代理",
  "evidence": "HTTP 403，响应包含 \"not available in your region\"",
  "degraded_mode": true
}
```

## 批量通知（看板/邮件）

> 本周新增 {n} 个地域受限源：{source_list}。覆盖国家：{countries}。若您需要这些媒体的完整内容，请在「设置 → 代理」中按国家添加出口。
