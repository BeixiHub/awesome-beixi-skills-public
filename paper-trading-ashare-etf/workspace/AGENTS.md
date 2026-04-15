# AGENTS.md - A 股 / ETF 模拟盘工作区

启动时先读 `SOUL.md`、`USER.md`、`TOOLS.md`，再决定是否加载 skill。

如果用户消息涉及以下任一意图，优先加载 `skills/paper-trading-ashare-etf/SKILL.md`：

- 模拟盘 / 虚拟盘 / 模拟交易
- 买入 / 卖出 / 下单
- 撤单 / 撤销委托 / 取消挂单
- 持仓 / 账户资产
- 今日委托 / 历史委托
- 今日成交 / 历史成交
- 实时价 / 行情

本 skill 支持用自然语言一次性完成查询、买入、卖出、撤单等操作，**不需要二次确认**。

默认测试账户已经内置在：

- `workspace/skills/paper-trading-ashare-etf/state/account_config.json`

因此进入本 workspace 的 agent 应默认直接使用这份配置做查询和交易，不需要等待用户再次提供 `user_token` 或 `account_id`。
