# Security Policy

## 支持版本

| 版本 | 状态 |
|---|---|
| main / develop | 活跃开发，安全修复直接合入 |
| 已发布 release | 最新 minor 版本提供安全修复 |

## 报告漏洞

**请勿公开开 issue 报告安全漏洞。** 请通过 GitHub 的 Private Vulnerability Reporting（Security 标签页 → Report a vulnerability）提交，包含：影响范围、复现步骤、潜在利用方式。

## 响应 SLA

| 阶段 | 时限 |
|---|---|
| 确认收到 | 72 小时 |
| 初步评估与定级 | 7 天 |
| 修复或缓解方案 | 严重（Critical/High）：14 天；其他：30 天 |
| 公开披露 | 修复发布后 7 天，与报告者协调 |

## 范围说明

本项目是新闻抓取平台，重点关注：

- 代理凭据/LLM API Key 的存储与泄露（凭据只存本地密钥引用，不落明文、不进日志）
- 抓取目标返回的恶意内容（HTML/重定向链注入）
- 认证与注册审核绕过
- SSRF 风险（抓取 worker 访问任意 URL 的天然面）

部署者请注意：默认配置面向自托管内网/受信环境，公网部署前请完成 M4 加固清单。
