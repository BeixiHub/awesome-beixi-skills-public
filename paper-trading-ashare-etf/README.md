# paper-trading-ashare-etf（OpenClaw Skill）

这是一个给 OpenClaw 智能体使用的 A 股 / ETF 模拟盘交易 Skill。

它的定位不是投顾，而是交易执行员：根据用户自然语言完成查询、买入、卖出、撤单等操作，并按既定规则返回结果。

---

## Skill 定位

- 面向 OpenClaw 的交易执行能力组件
- 支持自然语言到交易动作的解析与落地
- 默认无二次确认（以 `SKILL.md` 规则为准）
- 输出以结构化 JSON 为主，便于 Agent 再加工为用户回复

不做的事：

- 选股推荐、涨跌预测、策略建议、风控分析
- 跨市场交易（非本 Skill 范围）

---

## 触发意图（何时加载）

当用户意图涉及以下内容时，OpenClaw 应优先加载本 Skill：

- 模拟盘账户/持仓/成交/委托查询
- 买入、卖出、撤单
- 实时价、股票名称与代码解析

工作区路由可参考：

- `workspace/AGENTS.md`
- `workspace/routing-rules.json`

---

## 核心文件

主要实现位于 `workspace/skills/paper-trading-ashare-etf`：

- `SKILL.md`：行为规范与硬规则（优先级最高）
- `trading_service.py`：CLI 入口，负责查询/下单/撤单编排
- `broker_client.py`：模拟盘交易后端客户端（牛股网）
- `market_data_client.py`：行情与标的检索（新浪公开接口）
- `state/account_config.json`：账户配置模板（不提交真实凭证）
- `state/inner_code_cache.json`：运行时生成的 `stock_code -> inner_code` 缓存
- `state/trade_audit_log.jsonl`：运行时生成的交易审计日志

---

## OpenClaw 调用约定

在本 Skill 目录执行命令：

```powershell
cd .\workspace\skills\paper-trading-ashare-etf
python .\trading_service.py <command> ...
```

常用命令：

```bash
# 查询
python trading_service.py account
python trading_service.py holdings
python trading_service.py deals-today
python trading_service.py delegates-today
python trading_service.py quote "贵州茅台"
python trading_service.py resolve-symbol "510300"

# 交易
python trading_service.py buy "600519" 100 --price 1467.50
python trading_service.py sell "600519" 100 --price 1468.00
python trading_service.py cancel 95400000
```

所有命令返回 JSON，供 Agent 解析后按对话模板输出。

---

## 配置来源

优先顺序：

1. 环境变量（若设置）
2. `state/account_config.json`

可用环境变量：

- `PAPER_TRADING_USER_TOKEN`
- `PAPER_TRADING_ACCOUNT_ID`
- `PAPER_TRADING_BASE_URL`（可选）

说明：仓库内的 `account_config.json` 是模板，真实凭证建议通过环境变量或本地未跟踪配置注入。

---

## 关键约束（Agent 必读）

- 买入数量必须是 100 股整数倍
- 卖出数量不能超过 `tradeable`
- `tradeable=0` 常见于 T+1 锁仓（当天买入次日可卖）
- 价格缺失时通常用实时价；实时价取不到需按规则报错或追问
- `innerCode` 未命中时先查缓存/持仓/历史，再按规则动态搜索
- 不允许编造股票代码、委托号或 innerCode

具体错误分支、拒绝话术、硬规则请以 `SKILL.md` 为准。

---

## 给维护者的建议

- 修改行为前先更新 `SKILL.md`，再改代码
- 提交前检查 `state/` 下是否包含敏感凭证
- 避免在仓库中明文暴露真实 `user_token`
- `state` 目录下运行产物（缓存/审计日志）不应提交到版本库

如果你希望，我可以再补一版「给 OpenClaw 的最小接入示例」（包含意图识别 -> 命令调用 -> 结果转述的完整流程图和伪代码）。
