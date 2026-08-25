---
name: milestone-gate-review
description: >
  Global News Radar 项目的里程碑门禁评审（Gate Review）流程。当里程碑 M0–M4 到达出口、
  需要对照验收标准出具 PASS/CONDITIONAL/FAIL 结论，或需要变更验收指标/阈值时使用。
  触发场景：「M1 做完了，过一下门禁」「出一份里程碑验收报告」「这个 KPI 达不到，申请调整」
  「按 harness 03 文档核对验收证据」。
---

# 里程碑门禁评审（Milestone Gate Review）

## 概述

每个里程碑必须通过门禁评审才能关闭并开启下一阶段。评审依据是 `docs/harness/03-acceptance-criteria.md` 的量化标准——**只认证据，不认口述**。产出物为一份门禁报告（模板见 `references/gate-report-template.md`），归档到 `docs/harness/gate-reports/`。

## 五步流程

### 1. 证据收集

- 自动化证据：CI 报告、测试覆盖率、`scripts/gate_check.py` 输出、源健康看板快照
- 人工证据：Golden Set 回归记录、抽检批次合格率、演示录屏
- 证据必须可复现（附命令/链接/快照哈希），截图单独无效

### 2. 指标核对

```bash
python3 scripts/gate_check.py --metrics evidence/m1_metrics.json --criteria criteria/m1.json --json
```

逐条对照：功能验收标准、非功能标准、Definition of Done 清单，三项全部过才 PASS。脚本只判量化项；DoD 中的文档/评审项由评审人手工勾选。

### 3. 结论分级

- **PASS**：全部达标 → 关闭里程碑，打 tag（如 `m1-crawl-core`），开启下一阶段
- **CONDITIONAL**：非关键项未达标（占比 ≤20% 且无安全/合规项）→ 限期 2 周补齐，逾期自动转 FAIL
- **FAIL**：关键项未达标 → 不开启下一阶段；触发 02-progress-control.md 的红灯流程（范围裁剪或延期决策）

### 4. 例外与变更

- 指标/阈值变更申请 = 修改验收标准本身：需说明原因、影响面、替代指标，Maintainer 多数同意，并在报告中留痕
- 禁止「先宣布通过再补数据」；禁止把 CONDITIONAL 当作事实上的 PASS 无限拖延

### 5. 归档与公示

- 报告合并进仓库 `docs/harness/gate-reports/m{N}-gate-report.md`
- 里程碑 issue 引用报告链接并关闭；release notes 引用报告结论

## 评审人规则

- 至少 2 名 Maintainer 签字，且其中 1 人未直接参与该里程碑核心开发（回避原则）
- 社区评审窗口：报告草案公示 ≥72 小时，接受 Comment 后定稿
