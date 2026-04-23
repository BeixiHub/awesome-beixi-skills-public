---
name: paper-trading-claw
description: 处理 A 股和 ETF 模拟盘的注册、账户查询、持仓查询、成交查询、委托查询、买入、卖出、撤单。只要用户提到开通模拟盘、注册模拟盘、模拟盘账户、模拟盘持仓、模拟盘交易、买卖股票、撤委托，就应该使用这个 skill。首次使用先在对话里收集手机号，然后直接注册模拟盘并开始交易，不要让用户自己填写 token，也不要把旧 userToken 当成牛股网 V2 的主凭证。
---

# 模拟盘交易 Skill

这个 skill 负责两件事：

1. 在对话里完成模拟盘注册
2. 注册完成后，用 `ClawToken` 做账户查询和交易

默认注册方式是本地直连牛股网：

- skill 本地生成 `ClawToken`
- 本地直连注册每次强制生成新的 `ClawToken`
- skill 本地加密手机号
- skill 直接调用 `POST /virtualtrade/ClawUser/Register`
- 注册成功后立即调用 `GetAccount`
- 把绑定信息写入 `state/binding_state.json`

只有在显式要求走网站后端时，才使用：

```bash
python trading_service.py register --phone "13800138000" --via-backend --save-state
```

## 使用约定

1. 除非用户明确说“注册模拟盘”或“开通模拟盘”，否则先运行：

```bash
python trading_service.py status
```

2. 如果 `status` 显示未绑定，就收集手机号，然后运行：

```bash
python trading_service.py register --phone "13800138000" --save-state
```

3. 如果用户已经给了姓名，可以一起带上：

```bash
python trading_service.py register --real-name "张三" --phone "13800138000" --save-state
```

4. 注册成功后，再做查询或交易。
5. 统一只调用：

```bash
python trading_service.py ...
```

6. 不要自己拼牛股网 HTTP 请求。
7. 不要再把旧 `userToken` 当成 V2 主凭证。
8. V2 主凭证是 `ClawToken`。
9. 本地直连注册不要复用旧 `ClawToken`，每次都生成新的，避免复用旧模拟盘账户。
10. 用户如果不知道价格，默认先按当前盘口价格处理；只有拿不到盘口行情时才追问价格。

## 状态约定

只使用这 3 个状态：

- `unbound`
- `registering`
- `active`

不确定时先运行：

```bash
python trading_service.py doctor
python trading_service.py status
```

## 注册流程

当用户首次使用、当前没有 `ClawToken`，或者明确说“帮我注册模拟盘/开通模拟盘”时，进入注册流程。

执行顺序：

1. 优先收集手机号
2. 如果上下文里已经有姓名就一起带上；没有也可以直接注册
3. 运行注册命令
4. 如有需要，再运行一次 `status`

标准命令：

```bash
python trading_service.py register --phone "13800138000" --save-state
python trading_service.py status --save-state
```

## 查询命令

```bash
python trading_service.py account
python trading_service.py holdings
python trading_service.py deals-today
python trading_service.py deals-history
python trading_service.py delegates-today
python trading_service.py delegates-history
```

## 标的解析与行情

```bash
python trading_service.py resolve-symbol "神州高铁"
python trading_service.py quote "神州高铁"
python trading_service.py resolve-inner-code "000008"
python trading_service.py seed-inner-code --stock-code "000008" --stock-name "神州高铁" --inner-code 8
```

## 交易命令

```bash
python trading_service.py buy "000008" 100 --price 2.50
python trading_service.py buy "600519" 100
python trading_service.py sell "000008" 100
python trading_service.py cancel 95449276
```

不提供价格时：

- 买入优先用卖一价
- 卖出优先用买一价
- 如果拿不到盘口，再退回最新价
- 仍然拿不到行情时，再向用户追问价格

## 结果解释规则

下单时不要只看 `success`。

要综合看：

- `code`
- `message`
- `delegatesToday`

如果 `code = 0` 且 `message` 里有“委托成功”或“资金冻结”，按成功处理。

撤单前先判断委托状态：

- 只有待成交委托才继续发起撤单
- 如果这笔单子已经成交、已撤销，或已经不是待成交状态，不要继续无意义重试

撤单时，只有明确确认成功，才能说“撤单成功”。否则只能说：

- “当前未确认撤单成功”

## 对用户的回复风格

回复用户时只说业务结论，不要默认暴露这些细节：

- 本地状态文件路径
- 内部字段名，如 `binding_status`
- 本地后端地址
- 原始 HTTP 请求细节

优先用这种表达：

- “模拟盘已经可用，可以继续交易。”
- “我来帮你查账户。”
- “我已经帮你挂单。”
- “这笔单子现在还没确认撤掉。”

## 参考文件

需要确认接口真实行为时，读：

- `references/api-corrected.md`

需要看环境变量和接入方式时，读：

- `references/setup.md`

需要看错误处理时，读：

- `references/error-handling.md`

需要看标准对话路径时，读：

- `references/examples.md`

