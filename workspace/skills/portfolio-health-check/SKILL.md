---
name: portfolio-health-check
description: 串联投资组合快速诊断、深度诊断和优化处方三个子技能。适用于用户希望按阶段逐步完成组合分析，并在每一阶段结束后决定是否继续下一阶段时使用。
---

# Portfolio Health Check Workflow

## 角色

这是总控技能，不负责单独输出完整分析结论。它只负责：

- 收集用户输入
- 决定先跑哪个子技能
- 在每一阶段结束后询问是否继续下一阶段
- 串联三个子技能完成完整的组合分析流程

## 子技能

- `portfolio-quick-diagnosis/`：快速诊断，负责持仓概览、集中度检查和总体评价。
- `portfolio-deep-diagnosis/`：深度诊断，负责量化/技术视角的进一步分析。
- `portfolio-optimization/`：优化处方，负责约束条件下的优化建议。

## 总规则

- 全程使用中文。
- 不要编造实时数据、最新持仓、精确市值或任何未提供、未验证的外部事实。
- 不要给出具体买卖指令。
- 始终区分“已知信息”和“基于假设的信息”。
- 每一阶段结束后，都要先问用户是否继续下一阶段。
- 如果用户明确拒绝继续，就停止在当前阶段。
- 如果信息不足，先追问完成当前阶段所需的最少信息。
- 最多进行 3 轮补充信息对话。

## 数据来源原则

- 第 1 阶段必须先调用 QVeris 做标的识别，至少补齐股票名称/代码互查、公司简介和行业简介。
- 第 1 阶段如 QVeris 无法返回结果，再用公开网络检索补全基础背景。
- 第 2、3 阶段必须优先调用 QVeris 获取所需的金融/市场/行业数据，再结合公开网络信息做交叉验证。
- 如果 QVeris 和公开网络信息冲突，优先标记冲突，不要强行合并成确定结论。
- 不要自己编造任何数值型结果；拿不到数据时，要明确写“暂无充分证据”。

## QVeris 代码工具

- 复用脚本：`scripts/qveris_client.py`
- 作用：封装 QVeris 的 search / execute 调用
- 用法：先设置 `QVERIS_TOKEN`，再用脚本的 `search` 或 `execute` 子命令
- 状态文件：`state/portfolio_state.json`
- 作用：自动保存第 1、2 阶段的当前结构化结果，只保留最新一版快照，方便后续 Python 脚本直接读取
- 运行清单：`state/artifact_manifest.json`
- 作用：记录当前缓存快照的指纹，新的取数会自动清空旧快照，避免文件越积越多
- THS 取数入口：`scripts/qveris_client.py ths-collect <codes> --rebalance-frequency <frequency>`

## QVeris 调用协议

当第 1、2 或第 3 阶段需要外部金融数据时，按以下方式调用 QVeris：

**第 1 阶段（标的识别）**：直接用 `identify` 命令，它会自动调用已确认的三个工具完成代码互查、公司简介和行业分类。

**第 2、3 阶段（行情/市场数据）**：先用 `search` 搜索工具，再根据返回的 `search_id`、`tool_id`、`params` 执行工具。

把返回结果整理成分析要点，不要直接把原始 JSON 贴给用户。

强制要求：

- 第 1 阶段只要开始标的识别，就必须至少调用一次 `scripts/qveris_client.py identify`。
- 第 2 阶段和第 3 阶段只要开始执行，就必须至少调用一次 `scripts/qveris_client.py search`。
- 不能因为“看起来已经知道答案”而跳过 QVeris。
- 如果第一次搜索没有合适工具，必须换 query 再搜一次后再决定是否继续。
- 如果 QVeris 返回了可执行工具，就优先执行，再做分析。
- 只有在 QVeris 确实找不到工具或无法返回可用结果时，才退回到公开网络信息。

### 常用命令

```bash
python .cursor/skills/portfolio-health-check/scripts/qveris_client.py identify "贵州茅台"
python .cursor/skills/portfolio-health-check/scripts/qveris_client.py identify "贵州茅台" "中国平安" "沪深300ETF"
python .cursor/skills/portfolio-health-check/scripts/qveris_client.py search "stock historical price data API" --limit 10
python .cursor/skills/portfolio-health-check/scripts/qveris_client.py ths-collect "600519.SH" --rebalance-frequency monthly
python .cursor/skills/portfolio-health-check/scripts/qveris_client.py ths-collect "600519.SH" --all
python .cursor/skills/portfolio-health-check/scripts/qveris_client.py state show
python .cursor/skills/portfolio-health-check/scripts/qveris_client.py state path
python .cursor/skills/portfolio-health-check/scripts/qveris_client.py execute <tool_id> <search_id> --parameters-json "{\"symbol\":\"AAPL\"}"
```

### 参数文件

`--parameters-file` 里放一个 JSON 对象，只包含工具要求的参数，例如：

```json
{
  "symbol": "AAPL"
}
```

### 选择规则

- 第 1 阶段直接用 `identify` 命令完成名称/代码互查、公司简介和行业分类。
- 第 2、3 阶段如果 `results[].tool_id` 里已经能看出明确工具，就直接执行。
- 如果返回多个相似工具，优先选 `params` 最少且最贴近当前问题的工具。
- 如果搜索结果没有合适工具，换一个更具体的 query 再搜，不要直接编造数据。

## 状态存储

- 第 1 阶段的 `identify` 结果会自动写入 `state/portfolio_state.json` 的 `stage1`。
- 第 2 阶段的 `market-search` 结果只保留当前一次调用的摘要，不做历史累积。
- 第 2 阶段的 THS 取数结果写入 `stage2.ths_data`，`state/artifacts/` 只保留当前快照的精简文件。
- 当新一轮取数参数不同，脚本会自动清空旧快照并重建文件。
- `state show` 可以直接查看当前保存的结构化结果。
- 后续如果要做 Python 分析，优先读取这个 state 文件，而不是重新跑一遍对话流程。

## 信息收集总则

每次收集信息都遵守这三条：

1. 先收集“完成当前阶段必须有”的信息，不提前问下一阶段内容。
2. 把同一轮需要问的问题合并成一条，避免碎片化追问。
3. 能从用户原文直接识别的内容不要重复确认，只有缺失项才问。

如果用户一次给了很多信息，先整理成结构化输入，再进入下一步。

## 阶段一需要收集的信息

阶段一的目标是把持仓信息整理清楚并完成确认表，所以必须尽量收齐以下内容：

- 持仓清单
- 每个持仓的资产名称
- 每个持仓可能的证券代码
- 每个持仓的输入形式：金额、比例、股数、仅名称
- 每个持仓对应的仓位或金额
- 现金占比
- 是否存在“剩余未动”“货币基金”“活期理财”等现金线索
- 哪些标的暂时无法确认

如果用户给出的持仓比例加总不足 100%，不能假设剩余部分是现金。必须追问用户：剩余部分是现金、货币基金，还是有其他持仓没有列出。只有在用户明确回答后，才能确定剩余部分的归属。

如果用户只给名称没给仓位，优先追问：

- 各标的大概占比，或
- 各标的大概金额

如果用户只给股数，优先追问：

- 大致市值，或
- 大致占比

如果代码不清楚，优先追问：

- 标的全称，或
- 交易所代码

阶段一结束时，必须得到一份可供确认的持仓表。

## 阶段二需要收集的信息

阶段二只在快速诊断完成后进行，目标是补齐深度诊断参数。

必须收集：

- 换仓频率，也就是你通常多久调整一次仓位，例如日内、每周、每月、每季、长期持有
- 仓位管理方式，也就是你更像择时、轮动、恒定比例、定投，还是核心-卫星结构
- 风险承受度，也就是你能接受多大的波动，偏保守、稳健、积极还是激进
- 投资期限，也就是你准备持有多久，少于 1 年、1-3 年、3-5 年还是 5 年以上

阶段二需要的数据要点：

- 相关行情或价格区间
- 主要标的的行业、风格和主题背景
- 相关性、波动、回撤或替代风险的依据
- 必要时的宏观或行业数据

这些数据要优先通过 QVeris 获取；如果 QVeris 没有对应工具，再用公开网络资料补充。

如果用户不理解这些项，优先用选项形式提问，不要让用户自由发挥太多。

## 阶段三需要收集的信息

阶段三只在深度诊断完成后进行，目标是收集优化约束。

必须收集：

- 可投资范围，也就是你允许我建议哪些市场或品种，例如 A 股、ETF、期货、期权、港股通、美股、加密货币
- 还能投入多少资金，也就是你现在是否还有余钱，或者大概还能追加 10-30%、30-50%、还是 50% 以上
- 这次优化的目标，也就是你更想要资产增值、稳定现金流、对冲已有风险，还是打新底仓

阶段三需要的数据要点：

- 可执行的资产或品种候选
- 各候选的基础属性、流动性和适配约束
- 对冲或分散方案所需的市场信息
- 如果涉及新品种或新账户，需要对应的权限或市场可达性信息

这些数据同样要优先通过 QVeris 获取；如果 QVeris 没有对应工具，再用公开网络资料补充。

如果用户不理解这些项，先让用户从几个常见目标里选，不要让用户用长段文字自由描述。

## 股票代码处理规则

如果用户只给了股票名称、ETF 名称或简称，没有给代码，可以先自己检索公开信息补全代码。

处理原则：

1. 优先根据名称识别常见代码。
2. 如果名称可能对应多个标的，先标记为待确认，再追问用户。
3. 如果能高置信度识别，就直接补上代码，不要强迫用户自己补。
4. 只有在名称歧义较大或无法确认时，才把代码标为“无法确认”。

## 强制流程

### 第 1 阶段：快速诊断

1. 收集持仓、现金占比和必要的基础信息。
2. 调用 `portfolio-quick-diagnosis/`。
3. 先输出名称/代码互查表和公司/行业简介，再输出持仓确认表与快速诊断结果。
4. 询问用户是否继续进行深度诊断。

### 第 2 阶段：深度诊断

仅在第 1 阶段完成且用户明确同意后进行。

1. 补充深度诊断所需参数。
2. 调用 `portfolio-deep-diagnosis/`。
3. 输出深度分析结果。
4. 询问用户是否继续进行优化处方。

### 第 3 阶段：优化处方

仅在第 2 阶段完成且用户明确同意后进行。

1. 补充约束条件。
2. 调用 `portfolio-optimization/`。
3. 输出优化建议。

## 顺序约束

- 必须先完成第 1 阶段，才能进入第 2 阶段。
- 必须先完成第 2 阶段，才能进入第 3 阶段。
- 不允许跳过阶段。
- 不允许并行执行阶段。
- 每一阶段结束后都必须询问是否继续下一阶段。
- 只有在当前阶段完成并得到明确同意后，才允许进入下一阶段。

## 阶段切换提示

每个阶段结束时都要明确询问：

- “是否继续进入下一阶段？”
- “如果继续，我会补充下一阶段所需信息并生成对应结果。”

## 参考

- 先看 `scripts/qveris_client.py`，它是实际可调用的代码
- `qveris.md`
- `scripts/qveris_client.py`
- `portfolio-quick-diagnosis/SKILL.md`
- `portfolio-deep-diagnosis/SKILL.md`
- `portfolio-optimization/SKILL.md`
