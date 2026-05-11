# AGENTS.md - paper-trading-claw Workspace

Startup order:
1. Read `SOUL.md`
2. Read `USER.md`
3. Read `TOOLS.md`
4. If the user message matches the routing intents below, load `skills/paper-trading-claw/SKILL.md` before answering

Route to `skills/paper-trading-claw/SKILL.md` when the user mentions any of:
- 模拟盘 / 模拟交易 / 开通模拟盘 / 注册模拟盘
- 买入 / 卖出 / 下单
- 撤单 / 撤销委托 / 取消挂单
- 持仓 / 账户 / 资产
- 今日委托 / 历史委托
- 今日成交 / 历史成交
- 实时价 / 行情 / 股票代码 / 股票名称解析

Workspace notes:
- this workspace is only for A-share / ETF paper trading
- do not expose local state paths or internal backend details to end users
- do not commit real runtime state, `.env`, or cached account bindings
- cancel requests should route here so the assistant can explain that cancel is disabled and query delegate/deal status instead
