# AGENTS.md - paper-trading-claw-server Workspace

Startup order:
1. Read `SOUL.md`
2. Read `USER.md`
3. Read `TOOLS.md`
4. If the user message matches the routing intents below, load `skills/paper-trading-claw-server/SKILL.md` before answering

Route to `skills/paper-trading-claw-server/SKILL.md` when the user mentions any of:
- 模拟盘 / 模拟交易 / 注册模拟盘 / 开通模拟盘
- 手机号验证 / 短信验证码 / 验证码 / 注册 / 绑定
- 买入 / 卖出 / 下单
- 持仓 / 账户 / 资产
- 今日委托 / 历史委托
- 今日成交 / 历史成交
- 实时价 / 行情 / 股票代码 / 股票名称解析 / innerCode

Workspace notes:
- this workspace is only for A-share / ETF paper-trading through the Beixi transfer-service API
- do not expose API tokens, local `.env` values, backend database paths, or raw service internals to end users
- do not commit real `.env`, logs, SQLite files, or cached account bindings
- cancel order execution is disabled; use delegate/deal queries to inspect order status
