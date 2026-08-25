# 地域封锁判定信号规则

## 信号分级表

| 级别 | 信号 | 判定条件 | 说明 |
|---|---|---|---|
| 强 | HTTP 451 | 状态码 = 451 | RFC 7725，「因法律要求不可用」，是地域/法律封锁最明确的信号 |
| 强 | 403 + 地域语料 | 403 且正文含地域封锁措辞 | 语料样例："not available in your region"、"country or region where we do not provide services"、"currently unavailable in your location" 及各语种等价表述 |
| 中 | 重定向到地域警告页 | 3xx 跳转目标 URL/标题含 geo、region、country 提示 | 如跳转至 /geo-block、/region-locked 或静态警告页 |
| 中 | 正文异常截断 | 同 URL 历史正文长度 p10 对比，骤降 ≥90% 且非偶发（连续 ≥3 次） | 常见于「摘要可访问、全文限本地区」的软封锁 |
| 中 | 多出口对照 | 非目标国出口失败（403/451），目标国可信出口（社区报告/对照数据）返回 200 | 确认级证据，需注意云服务商 WAF 也可能按 ASN 拦截 |
| 辅助 | 同 IP 重试复现 | 同出口 IP 重试 3 次结果不变 | 排除偶发故障 |
| 排除 | 反爬特征 | 429、5xx、Cloudflare JS 挑战、CAPTCHA | 命中即排除地域判定，按 anti_bot 处理 |

## Verdict 规则

1. 命中「排除」信号 → `anti_bot`（终止地域判定）
2. 命中任一强信号 + 辅助复现 → `geo_restricted`
3. 中信号 ≥2 且无反爬特征 → `geo_restricted`
4. 其余 → `inconclusive`

## blocked_legal 识别

- 目标媒体所在国对部署地实施制裁/法律禁运，或媒体内容本身在部署地属非法 → `blocked_legal`
- 典型线索：451 且语料明确引用法规（如 "due to legal reasons in your country"）、OFAC 制裁国列表、EU 对俄媒禁令
- 处置：不抓、不提示代理、在源注册表标记 `blocked_legal` 并附法条/新闻来源
