---
name: geo-block-triage
description: >
  Global News Radar 项目的地域 IP 限制（geo-blocking）处置流程。当某媒体源疑似仅允许本国 IP 访问、
  抓取返回 451/403/地域重定向/正文异常截断，或需要为用户生成「需要添加 xx 地域代理」提示时使用。
  触发场景：「这个源 403 了是不是地域封锁」「给受限源写用户提示文案」「配置 xx 国家的代理绑定」
  「复核 geo_restricted 判定是否正确」。
---

# 地域受限处置（Geo-block Triage）

## 概述

把「疑似地域封锁」转化为三选一的确诊结论：`geo_restricted`（需用户配置代理）、`anti_bot`（反爬拦截，换抓取策略）、`blocked_legal`（法律/制裁封锁，不抓取）。核心红线：**系统只检测与提示，不静默切换代理，绕过与否的决策权留给用户**。

## 四步流程

### 1. 信号采集

对同一 URL 记录：HTTP 状态码、响应正文语料（前 2KB）、重定向链、响应头（`CF-IPCountry` 等）、正文长度与历史均值对比、不同网络出口（如有）的对照结果。信号分级规则见 `references/signal-rules.md`。

### 2. 计分判定

```bash
python3 scripts/geo_verdict.py --status 451 --body-hit "not available in your region" \
    --redirect-geo --len-ratio 0.1 --json
```

按脚本输出的 verdict 执行：
- `geo_restricted`（强信号命中，或中信号≥2 且排除反爬）→ 进入第 3 步
- `anti_bot`（429/5xx/JS 挑战，Cloudflare 挑战页）→ 转入抓取策略问题，不标记地域受限
- `inconclusive` → 标记 `geo.status: unknown`，24 小时后复测，连续 3 次 inconclusive 升级为人工复核

### 3. 生成用户提示

按 `references/message-templates.md` 生成提示，三要素缺一不可：**受限地域**（ISO 国家码转国家名）、**证据**（状态码/语料样例）、**配置入口**（指向代理配置页的链接/命令）。文案用陈述句，不出现「绕过」「破解」等字眼，统一表述为「该媒体仅允许 xx 地区 IP 访问」。

### 4. 代理绑定与复核

- 绑定优先级：源级 > 国家级 > 全局；用户未配置前，该源进入受限降级模式（仅经聚合层获取标题+URL 级数据）
- 用户配置代理后必须重试验证：同一 URL 经代理请求返回 200 且正文抽取成功 → `geo.status` 恢复 `ok`，记录所用代理级别（数据中心/住宅）
- 凭据只存本地密钥引用，不落明文，不进日志

## 质量红线

- 法律/制裁封锁（`blocked_legal`）一律不抓，不提示代理——避免诱导违法访问
- 不把反爬 403 误判为地域封锁：反爬换出口 IP 通常仍 403，地域封锁换到目标国出口即 200
- 判定证据必须留档（`geo.evidence`），供社区复核与误判回滚
