---
name: portfolio-deep-diagnosis
description: 收集 4 个参数后调用远端 Phase 2 API，返回深度诊断并默认生成 PDF。
---

# 投资组合深度诊断

## 前提

阶段一已完成，`state/portfolio_state.json` 中有 `stage1` 结果。

## 收集参数

| 收集项 | JSON 字段 | 可选值 | 中文选项 |
|--------|----------|--------|---------|
| 换仓频率 | `rebalance_frequency` | `intraday` / `weekly` / `monthly` / `quarterly` / `buy_and_hold` | 日内 / 周度 / 月度 / 季度 / 长期持有 |
| 仓位管理风格 | `position_style` | `market_timing` / `full_rotation` / `constant_mix` / `dca` / `core_satellite` | 择时空仓型 / 满仓轮动 / 恒定比例 / 定投渐进 / 核心+卫星 |
| 风险承受力 | `risk_tolerance` | `conservative` / `moderate` / `aggressive` / `very_aggressive` | 保守 / 稳健 / 积极 / 激进 |
| 投资期限 | `investment_horizon` | `<1y` / `1-3y` / `3-5y` / `>5y` | <1年 / 1-3年 / 3-5年 / 5年以上 |
| 总投资金额（可选） | `portfolio_market_value` | 正数（元） | 用户输入万元，转换为元 |

用户不理解时给选项，不开放式追问。最多补问 3 轮。

## 执行

1. 补齐参数 → 组装 payload → 默认调用 `phase2_pdf` 生成 PDF 报告。
2. 仅在 `phase2_pdf` 接口失败时，改用 `phase2` 返回 JSON 数据并用语言描述结果。
3. 用中文解释结果，优先使用 `client_output`，不贴原始 JSON。
4. 收口：只问是否继续进入优化处方。
