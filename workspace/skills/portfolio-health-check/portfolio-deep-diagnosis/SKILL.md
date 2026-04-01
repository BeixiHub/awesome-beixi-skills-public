---
name: portfolio-deep-diagnosis
description: 用于对投资组合进行深度诊断、参数补全、量化/技术视角分析和风险识别。适用于用户请求深度诊断、相关性分析、风险分析、因子暴露或 `deep-diagnosis` 风格报告时。
---

# 投资组合深度诊断

## 目的

把持仓与分析参数整理成一份结构化的深度诊断结果，重点输出相关性、风险、因子暴露和关键风险点。

## 适用场景

当任务是以下内容时使用本技能：

- 进一步分析组合的波动、相关性和风险结构
- 结合参数判断持仓管理风格
- 识别组合中的风格暴露和因子偏向
- 输出更偏量化/技术视角的诊断结论

## 总体分工

- `SKILL.md`：负责流程控制、参数收集、确认步骤和调用顺序。
- `analysis_prompt.md`：负责分析逻辑、数据检索范围和风险判断。
- `report_prompt.md`：负责最终报告格式、章节结构和输出措辞。

## 核心规则

- 全程使用中文。
- 不要声称自己知道实时价格、最新基金持仓，或任何未提供、未验证的外部事实。
- 不要给出买卖指令，也不要预测市场走势。
- 清楚区分已知事实和粗略假设。
- 深度诊断必须先收集 4 个参数：
  - `rebalance_frequency`
  - `position_style`
  - `risk_tolerance`
  - `investment_horizon`
- 如果参数不足，先追问最少必要信息。
- 最多进行 3 轮补充信息对话。

## 数据获取规则

- 深度诊断阶段必须先调用 QVeris，不能跳过。
- 至少执行一次 `scripts/qveris_client.py search`，再决定是否继续。
- 优先获取：行情或价格区间、行业背景、相关性依据、波动/回撤线索、宏观或行业数据。
- 如果 QVeris 无法返回所需数据，再使用公开网络资料交叉验证。
- 如果两者冲突，明确标记冲突，不要强行下结论。
- 如果数据仍然不足，要明确写“暂无充分证据”。

## QVeris 调用步骤

1. 用 `scripts/qveris_client.py search` 搜索与你要的数据相匹配的工具。
2. 读取返回中的 `search_id`、`tool_id`、`params`、`examples`。
3. 按 `params` 准备参数文件，优先用 `--parameters-file` 执行。
4. 执行后把原始返回整理成相关性、波动、风险和背景要点。
5. 如果第一次搜索不到合适工具，换一个更聚焦的 query 再搜。
6. 只有在 QVeris 确实找不到可用工具时，才退回到公开网络资料。

### 推荐 query

- `historical price data API`
- `stock correlation and volatility API`
- `industry sector classification API`
- `market macro data API`

## 按换仓频率取数

深度诊断必须根据 `rebalance_frequency` 选择不同的数据粒度。默认使用 `close` 和 `volume` 两类数据。

| 换仓频率 | 主数据（交易分析） | 辅助数据（宏观视角） |
|---------|----------------|-------------------|
| 日内 | 15分钟 / 3个月 | 日线 / 1年 |
| 周度 | 日线 / 1年 | — |
| 月度 | 日线 / 2年 | — |
| 季度 | 周线 / 3年 | — |
| 长期持有 | 月线 / 5年 | — |

### THS 调用规则

- 15 分钟数据使用 `ths_ifind.hf_basic_quotation.v1`
  - 参数：`codes`、`starttime`、`endtime`、`interval="15"`
- 日线 / 周线 / 月线 / 3 个月 / 1 年 / 2 年 / 3 年 / 5 年数据使用 `ths_ifind.history_quotation.v1`
  - 参数：`codes`、`startdate`、`enddate`、`interval="D"|"W"|"M"`，历史行情优先只取 `close` 和 `volume`
- 15 分钟和历史行情都要先保存精简后的结构化结果，只保留 `code/time/close/volume`
- 同一批数据写入 `state/portfolio_state.json` 的 `stage2.ths_data`
- `state/artifacts/` 只保留当前快照的精简后的摘要和行数据
- 新参数触发新一轮取数时，脚本会自动清空旧快照，避免缓存散落

### 执行顺序

1. 先用 `scripts/qveris_client.py ths-collect <codes> --rebalance-frequency <frequency>` 生成对应数据。
2. 如果要一次性测试全覆盖，可以加 `--all`，会把 15 分钟、日线、周线、月线和 3 个月 / 1 年 / 2 年 / 3 年 / 5 年都取一遍。
3. 如果相同参数的 artifact 已存在，脚本会直接复用，不再重复调用接口。
4. 如果参数变化，脚本会清空旧快照并重建当前这一版文件。
5. 调用后把结果整理成结构化摘要，再写回 `state/portfolio_state.json`。
6. 分析时只看 `close` 和 `volume`，不再保留其他行情字段。
7. 如果 `rebalance_frequency` 是 `intraday`，必须同时保留 15 分钟 / 3 个月和日线 / 1 年两套数据。

### 状态存储

- 第 2 阶段的 `market-plan` 和 `market-search` 结果只保留当前一版，不再累积历史调用。
- 这份文件会保留第 1 阶段写入的内容，因此后续 Python 脚本可以直接同时读取 stage1 + stage2。
- 如果重新运行第 2 阶段，应该覆盖同一个 stage2 区块，并清空旧快照。

## 信息收集流程

1. 识别持仓和现金占比。
2. 收集深度诊断参数。
3. 如有需要，补充说明持仓集中度、风格偏好或用户关注重点。
4. 调用 THS / QVeris 和公开网络资料补充所需数据。
5. 生成深度诊断分析结果。

## 参数识别

`params` 字段建议按以下方式收集：

- `rebalance_frequency`：`intraday` / `weekly` / `monthly` / `quarterly` / `buy_and_hold`
- `position_style`：`market_timing` / `full_rotation` / `constant_mix` / `dca` / `core_satellite`
- `risk_tolerance`：`conservative` / `moderate` / `aggressive` / `very_aggressive`
- `investment_horizon`：`<1y` / `1-3y` / `3-5y` / `>5y`

## 调用顺序

1. 先确认持仓信息和现金占比。
2. 追问深度诊断参数。
3. 先用 THS / QVeris 检索所需数据，再用公开网络资料补充或交叉验证。
4. 调用 `analysis_prompt.md` 生成分析要点。
5. 再调用 `report_prompt.md` 生成最终报告。
6. 输出完成后，把控制权交回父级 workflow。

## 边界规则

- 不得编造历史收益、波动率、回撤、相关系数或因子暴露数值。
- 如果这些量化信息无法从用户提供信息或公开来源中合理得到，要明确写“暂无充分证据”。
- 可以做基于常识的风险判断，但必须标注为推断。

## 参考文件

- [qveris.md](../../../../qveris.md)
- [qveris_client.py](../../scripts/qveris_client.py)
- [analysis_prompt.md](analysis_prompt.md)
- [report_prompt.md](report_prompt.md)
