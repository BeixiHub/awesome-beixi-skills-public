---
name: portfolio-optimization
description: 收集约束后调用远端 Phase 3 API，输出优化处方。
---

# 投资组合优化处方

## 前提

阶段二已完成，深度诊断完整输出可用。

## 收集约束

| 收集项 | JSON 字段 | 可选值 | 中文选项 |
|--------|----------|--------|---------|
| 可投资市场 | `allowed_markets` | `A-share` / `HK` / `US` | A股 / 港股通 / 美股（多选） |
| 可使用工具 | `allowed_instruments` | `stock` / `etf` / `fund` / `futures` / `option` / `crypto` | A股 / ETF / 基金 / 期货 / 期权 / 加密货币（多选） |
| 剩余可投资金 | `additional_capital_ratio` | `none` / `10-30%` / `30-50%` / `50%+` | 满仓无余量 / 还有10-30% / 还有30-50% / 还有50%以上 |
| 投资目标 | `objectives` | `growth` / `income` / `hedge` / `ipo_base` | 资产增值 / 稳定现金流 / 对冲已有风险 / 打新底仓（多选） |
| 可接受敞口（可选） | `allowed_exposure` | `A-share` / `HK` / `US` / `global` | 默认 = 可投资市场 + global |
| 已开通权限（可选） | `account_permissions` | `option_account` / `futures_account` / `hk_connect` / `qdii` | — |

用户不理解时给常见选项，不让长篇自由描述。最多补问 3 轮。

## 执行

1. 补齐约束 → 用 Phase 2 完整输出 + 约束组装 payload → 调用 `phase3` API。
2. 用中文解释结果，优先使用 `client_output`，按优先级解释建议层级。
3. 收口：结果解释完即结束。
