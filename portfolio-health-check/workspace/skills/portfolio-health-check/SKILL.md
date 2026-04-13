---
name: portfolio-health-check
description: 串联投资组合快速诊断、深度诊断和优化处方。默认先执行当前阶段任务，完成后只提下一个阶段所需的关键问题。
---

# Portfolio Health Check Workflow

## 角色

总控技能，负责收集输入 → 调用子技能 → 收口询问是否继续。不替代子技能执行。

## 模式

- **对话模式**：先做当前阶段，再问是否继续。最多补问 3 轮。
- **API 模式**：不追问，直接执行。

## 通用规则

- 全程中文。不编造数据。不给买卖指令。
- 第 2、3 阶段只调用远端 API，接口不可用时直接返回错误，不降级。
- Phase 2 完成后默认调用 `phase2_pdf` 生成 PDF 报告。

## 阶段一：快速诊断

收集持仓清单、仓位、现金归属。至少调用一次 QVeris `identify`。
→ 输出持仓确认表 + 快速诊断结论。

## 阶段二：深度诊断

收集 4 个参数，默认调用 `phase2_pdf` 生成 PDF 报告。仅在 PDF 接口失败时改用 `phase2` 返回 JSON 数据。

| 收集项 | JSON 字段 | 可选值 |
|--------|----------|--------|
| 换仓频率 | `rebalance_frequency` | `intraday` / `weekly` / `monthly` / `quarterly` / `buy_and_hold` |
| 仓位管理风格 | `position_style` | `market_timing` / `full_rotation` / `constant_mix` / `dca` / `core_satellite` |
| 风险承受力 | `risk_tolerance` | `conservative` / `moderate` / `aggressive` / `very_aggressive` |
| 投资期限 | `investment_horizon` | `<1y` / `1-3y` / `3-5y` / `>5y` |
| 总投资金额（可选） | `portfolio_market_value` | 正数（元） |

## 阶段三：优化处方

收集约束，调用 `phase3` API。

| 收集项 | JSON 字段 | 可选值 |
|--------|----------|--------|
| 可投资市场 | `allowed_markets` | `A-share` / `HK` / `US` |
| 可使用工具 | `allowed_instruments` | `stock` / `etf` / `fund` / `futures` / `option` / `crypto` |
| 剩余可投资金 | `additional_capital_ratio` | `none` / `10-30%` / `30-50%` / `50%+` |
| 投资目标 | `objectives` | `growth` / `income` / `hedge` / `ipo_base` |
| 可接受敞口（可选） | `allowed_exposure` | `A-share` / `HK` / `US` / `global` |
| 已开通权限（可选） | `account_permissions` | `option_account` / `futures_account` / `hk_connect` / `qdii` |

## 顺序约束

- 正常流程：1 → 2 → 3，每阶段结束后收口再继续。
- 快捷流程：用户希望跳过分析直接获取优化处方时，静默调用 `phase2` API 获取 Phase 3 所需的 `diagnosis_result`，不向用户展示 Phase 2 结果，直接进入 Phase 3 收集约束并调用 `phase3` API。
- Phase 1（持仓确认）不可跳过。
