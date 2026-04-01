---
name: portfolio-optimization
description: 用于基于约束条件生成投资组合优化处方、分层建议和前后对比。适用于用户请求优化建议、配置优化、分层处方或 `optimization` 风格报告时。
---

# 投资组合优化处方

## 目的

在持仓现状、深度诊断结果和用户约束条件基础上，生成分层优化建议与前后对比。

## 适用场景

当任务是以下内容时使用本技能：

- 生成组合优化建议
- 依据约束条件筛选可行动作空间
- 给出零成本、需额外资金、需新品种三级建议
- 输出优化前后对比的结构化报告

## 总体分工

- `SKILL.md`：负责流程控制、约束收集、确认步骤和调用顺序。
- `analysis_prompt.md`：负责优化逻辑、约束解读和建议生成。
- `report_prompt.md`：负责最终报告格式、章节结构和输出措辞。

## 核心规则

- 全程使用中文。
- 不要编造实时价格、最新持仓、精确市值或任何未提供、未验证的外部事实。
- 不要给出具体买卖指令。
- 所有建议都必须受用户约束限制。
- 必须先收集 `constraints`：
  - `investable_markets`
  - `available_capital`
  - `objectives`
- 如果约束不足，先追问最少必要信息。
- 最多进行 3 轮补充信息对话。

## 数据获取规则

- 优化处方阶段必须先调用 QVeris，不能跳过。
- 至少执行一次 `scripts/qveris_client.py search`，再决定是否继续。
- 优先获取：可投资市场可达性、品种基础属性、流动性线索、相关对冲或分散方案的可行性。
- 如果 QVeris 无法覆盖，再用公开网络资料补充。
- 如果数据冲突或不足，要明确提示，不要编造结论。

## QVeris 调用步骤

1. 用 `scripts/qveris_client.py market-search <rebalance_frequency>` 搜索候选品种、市场可达性或约束相关工具。
2. 读取 `search_id`、`tool_id`、`params`、`examples`。
3. 根据用户约束准备参数文件，优先用 `--parameters-file` 执行。
4. 把返回结果整理成可执行候选、不可行候选和分层建议。
5. 如果第一次搜索不到合适工具，换一个更贴近优化目标的 query 再搜。
6. 只有在 QVeris 确实找不到可用工具时，才退回到公开网络资料。

### 推荐 query

- `asset allocation and portfolio optimization API`
- `ETF liquidity and fund data API`
- `market accessibility and trading permission API`
- `risk hedge instrument data API`

## 信息收集流程

1. 识别当前持仓、现金和深度诊断结果。
2. 收集约束条件。
3. 识别用户可以接受的可投资市场、资金余量和目标。
4. 调用 QVeris 和公开网络资料补充候选与约束数据。
5. 生成分层优化建议。

## 约束识别

`constraints` 字段建议按以下方式收集：

- `investable_markets`：`a_share` / `etf` / `futures` / `options` / `hk_connect` / `us_stock` / `crypto`
- `available_capital`：`none` / `10-30%` / `30-50%` / `>50%`
- `objectives`：`capital_growth` / `stable_cashflow` / `hedge_existing_risk` / `ipo_ballast`

## 调用顺序

1. 先确认持仓、现金和深度诊断结论。
2. 追问优化约束条件。
3. 先用 QVeris 检索候选数据，再用公开网络资料补充或交叉验证。
4. 调用 `analysis_prompt.md` 生成优化要点。
5. 再调用 `report_prompt.md` 生成最终报告。
6. 输出完成后，把控制权交回父级 workflow。

## 边界规则

- 优化建议只能停留在资产配置和策略层面。
- 如果用户约束太强，允许输出“不可行”或“暂不建议”。
- 如果需要使用行情/公开资料，只能做方向性判断，不能把不确定内容写死。

## 参考文件

- [qveris.md](../../../../qveris.md)
- [qveris_client.py](../../scripts/qveris_client.py)
- [analysis_prompt.md](analysis_prompt.md)
- [report_prompt.md](report_prompt.md)
