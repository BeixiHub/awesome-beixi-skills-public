# TOOLS.md

本工作区的 paper-trading-ashare-etf 交易 skill 使用以下配置来源：

- `skills/paper-trading-ashare-etf/state/account_config.json`
- 环境变量 `PAPER_TRADING_USER_TOKEN`
- 环境变量 `PAPER_TRADING_ACCOUNT_ID`

**无需任何第三方 API Key。** 行情和股票名称识别通过新浪财经公开接口获取（`suggest3.sinajs.cn` + `hq.sinajs.cn`），免鉴权、仅依赖 Python 标准库。

默认行为：

- 优先直接读取 `skills/paper-trading-ashare-etf/state/account_config.json`
- 这份文件里已经写入一个可用的测试模拟盘账户
- 只有当用户明确要求切换账户时，才改用新的 `user_token` / `account_id`

不要在代码里写死真实凭证。

## 依赖模块

skill 目录下的三个 Python 模块：

| 文件 | 作用 | 依赖 |
|---|---|---|
| `broker_client.py` | 模拟盘交易后端客户端（下单/撤单/查询） | stdlib only |
| `market_data_client.py` | 行情 + 标的检索（新浪公开接口） | stdlib only |
| `trading_service.py` | 业务编排层（校验、解析、组装、审计） | 上述两个客户端 |

## 如果新浪接口暂时不可用

新浪 `hq.sinajs.cn` / `suggest3.sinajs.cn` 在极少数情况下可能被限流或临时不可达。此时：

- 仍可查询账户、持仓、成交、委托（这些只走牛股网模拟盘后端）
- 仍可撤单（撤单只需 `userToken` + 委托单 id）
- 仍可用 **6 位代码 + 显式限价** 下单已持仓 / 已缓存 inner_code 的股票
- 不可按股票名称下单（没法做 name → code 解析）
- 不可下市价单（拿不到实时价）
- 不可首次买入全新标的（innerCode 反查需要涨跌停价做校验）

这种情况下应该原样告诉用户"行情服务暂时不可用"，让用户提供 6 位代码和显式限价继续操作。
