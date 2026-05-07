---
name: paper-trading-claw-server
description: Client skill for OpenClaw/ArkClaw-compatible agents to use the Beixi Niuguwang A-share/ETF paper-trading API. Use when a user asks an agent to send SMS verification, verify a code, register or bind a paper-trading account, query account status, holdings, deals, delegates, usage, resolve stock symbols or innerCode, fetch quotes, or place virtual buy/sell orders. Do not use for real-money trading; cancel orders are disabled.
---

# Paper Trading Claw Skill

这是给智能体使用的 A 股/ETF 模拟盘 skill。当前版本不在本地直连牛股王，也不在本地保存用户手机号、clawToken 或交易状态；所有注册、短信验证、账户查询、下单和用户数据持久化都通过后端 API 完成。

## 运行原则

- 后端 API 是唯一可信状态源，用户绑定、短信会话、innerCode 缓存、交易审计都保存在服务端 SQLite 数据库。
- 不要直接调用牛股王接口，不要把火山短信 AK/SK 放进 skill 目录。
- 手机号注册必须走 `send-sms -> verify-code -> register`。
- 注册链路响应里如果出现 `mandatoryNotice`，必须把 `mandatoryNotice.text` 原样展示给用户，再继续后续操作。
- 下单前必须确认用户已经完成绑定，且有可用的 `innerCode`。如果自动解析失败，要求用户提供 `innerCode` 或先查一次持仓/委托让后端回填。
- 撤单功能已下线，不要向用户承诺可以撤单；需要查看订单状态时使用 `delegates-today` 和 `deals-today`。
- 所有命令默认使用 `PAPER_TRADING_USER_ID`。上架到平台后，如果平台能提供真实用户 ID，必须通过 `--user-id` 传入，避免不同用户共享同一个测试 ID。
- 需要安装和环境变量细节时，读取 `references/setup.md`。
- 需要接口字段和路径时，读取 `references/api.md`。
- 需要解释错误码时，读取 `references/error-handling.md`。

## 环境变量

在本目录创建 `.env`，参考 `.env.example`：

- `PAPER_TRADING_API_BASE_URL`: 后端地址，当前测试环境使用 `https://admin.deepseekdata.com`
- `PAPER_TRADING_API_TOKEN`: 后端 API token，如果后端启用了鉴权必须配置
- `PAPER_TRADING_USER_ID`: 当前智能体用户 ID，默认 `local-user`；上架平台应覆盖为真实业务用户 ID

## 常用命令

检查连接：

```bash
python trading_service.py doctor
```

发送验证码：

```bash
python trading_service.py send-sms --user-id USER_001 --phone 13800138000 --real-name 张三
```

校验验证码：

```bash
python trading_service.py verify-code --user-id USER_001 --claw-token pt_xxx --phone 13800138000 --code 123456
```

注册并绑定模拟盘：

```bash
python trading_service.py register --user-id USER_001 --claw-token pt_xxx
```

查询状态、账户、持仓、委托：

```bash
python trading_service.py status --user-id USER_001
python trading_service.py account --user-id USER_001
python trading_service.py holdings --user-id USER_001
python trading_service.py delegates-today --user-id USER_001
```

解析股票和行情：

```bash
python trading_service.py resolve-symbol 神州高铁 --user-id USER_001
python trading_service.py quote 000008 --user-id USER_001
```

保存 innerCode：

```bash
python trading_service.py seed-inner-code --user-id USER_001 --stock-code 000008 --stock-name 神州高铁 --inner-code 8
```

买入、卖出：

```bash
python trading_service.py buy 000008 --user-id USER_001 --quantity 100 --price 2.50 --inner-code 8
python trading_service.py sell 000008 --user-id USER_001 --quantity 100 --price 2.60 --inner-code 8
```

## 智能体交互建议

- 用户第一次使用时，先询问真实姓名和手机号，然后发送验证码。
- `send-sms`、`verify-code`、`register` 的服务端响应会强制携带一段固定提示；展示给用户，不要改写。
- 用户给出验证码后，调用 `verify-code`，成功后立即调用 `register`。
- 后续交易不要再让用户重复手机号验证，直接用后端保存的绑定状态。
- 下单前把标的、数量、价格、买卖方向复述给用户确认。
- 如果返回 `INNER_CODE_REQUIRED`，让用户提供 `innerCode`，或先查询持仓/委托。
- 如果返回 `QUOTE_REQUIRED`，让用户给出明确限价。
- 如果用户要求撤单，说明撤单功能已下线，并改为查询今日委托/成交状态。
