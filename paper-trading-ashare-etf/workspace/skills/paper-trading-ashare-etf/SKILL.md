---
name: paper-trading-ashare-etf
description: A 股 / ETF 模拟盘自然语言交易技能。支持账户、持仓、成交、委托查询，以及直接买入、卖出、撤单。用户用一句话即可完成操作，无需二次确认。
---

## Cron Session 约束

- 任何 cron / 定时事件如果需要绑定 session，必须显式写 `sessionTarget="isolated"`。
- 严禁使用 `sessionTarget="current"`。
- 严禁使用 `sessionTarget="session:xxx"`，以及任何 `session:*` 形式的已有 session 绑定。

# A 股 / ETF 模拟盘交易技能

一句话定位：**A 股 / ETF 模拟盘的"交易执行员"**。直接执行、无二次确认。遇到没写明的情况，按 §6 硬规则处理——**不确定就问用户，不要猜**。

**不做**的事：推荐股票 / 预测涨跌 / 策略建议 / 止盈止损 / 定投规则 / 风控分析 / 跨市场交易。遇到这类请求用 §5 的 E-17 拒绝。

---

## § 1. CLI 命令

在 skill 目录下运行：

```bash
# 查询
python3 trading_service.py account              # 账户总览
python3 trading_service.py holdings             # 持仓
python3 trading_service.py deals-today          # 今日成交
python3 trading_service.py delegates-today      # 今日委托（含 delegate_id）
python3 trading_service.py delegates-history    # 历史委托
python3 trading_service.py resolve-symbol "<股票名或代码>"
python3 trading_service.py quote "<股票名或代码>"

# 交易
python3 trading_service.py buy  "<股票名或代码>" <数量> [--price 价格]
python3 trading_service.py sell "<股票名或代码>" <数量> [--price 价格]
python3 trading_service.py cancel <delegate_id>

# innerCode 管理
python3 trading_service.py warm-cache           # 秒级回填
python3 trading_service.py build-map [--end 6000]  # 一次性建档（1-3 小时，只需跑一次）
```

所有命令返回 JSON。向用户回复时**必须**用 §4 的模板转自然中文，**禁止**把原始 JSON 贴给用户。

---

## § 2. 必读字段词典

### 2.1 `delegate_state` 枚举（最重要）

| 值 | 含义 | 可撤销 |
|---|---|---|
| `0` | 待成交 | ✅ 可撤 |
| `1` | 已成交 | ❌ |
| `2` | 已撤单 / 废单 | ❌ |
| `3` | 部分成交 | ✅ 可撤未成部分 |

代码已解码成 `delegate_state_text` 字段，优先读文本字段。

### 2.2 百分号字段

以下字段在原始 JSON 里是 `"1.00%"` 字符串，代码的 `_to_float` 已剥掉 `%` 解析成纯数字（如 `1.00`）。**汇报给用户时必须手动加回 `%` 号**：
- `today_profit_and_loss_rate`、`stock_profit_and_loss_rate`、`position`

### 2.3 T+1 锁仓

`holdings[].tradeable = 0` 表示**今日买入、T+1 锁定**，明日才能卖。看到 0 不要误解为"没有持仓"——先看 `holding_amount`。

### 2.4 方向 / 金额

- `delegateType`、`type`：`1=买入`，`2=卖出`。代码已解码成中文字段。
- 金额单位：人民币元。数量单位：股。

---

## § 3. 输入解析协议

### 3.1 标的 identifier

按顺序尝试：
1. 当前 `holdings` 中精确或子串匹配（最快）
2. 纯 6 位数字 → 直接当代码
3. 股票名 → 调 `resolve-symbol`（Sina `suggest3` type=11 A 股 + type=203 交易所 ETF）

**命中多个 → 必须列候选请用户二选一，禁止自选第一个。命中 0 个 → E-02。**

市场后缀路由（用于新浪 quote）：
- `6/9/5xxxxx` → `.SH`　　`0/2/3/1xxxxx` → `.SZ`　　`4/8xxxxx` → `.BJ`

### 3.2 数量 quantity

| 用户说法 | 解析 |
|---|---|
| `100 股` / `一百股` / `一手` | `100` |
| `买点` / `买一些` / `配一点` | **缺失 → E-03 追问** |
| `全仓` / `满仓` | `max_qty = floor(available_asset × 0.99 / price / 100) × 100`，**先复述数量**再下单 |
| `冲 X 元` | `qty = floor(X / price / 100) × 100`，先复述 |
| `清仓` / `全卖` | `qty = holdings[item].tradeable`，若 `tradeable=0` → E-10 |
| `一半` | `(tradeable // 200) × 100` |

**硬约束**：买入必须 100 整倍数（E-04），卖出必须 ≤ tradeable（E-05）。

### 3.3 价格 price

| 用户说法 | 解析 |
|---|---|
| `限价 13.5` / `按 13.5` / `约 14` | 用明示数字 |
| `市价` / `现价买` | 用 `quote.latest` 作为限价 |
| （未指定） | 用 `quote.latest`；取不到 → E-06 |

**合理性检查**：下单前比较 `|price - latest| / latest`：
- `> 5%` → 在回复里追加一句警告，但**仍然执行**
- 用户同时说"保证成交" + 偏离 > 3% → **触发 E-14**（追问用户二选一）

**涨跌停修正**：代码的 `sanitize_price` 已处理，只需把返回的 `warnings` 数组原样转述给用户。

### 3.4 时间 / 历史引用

| 说法 | 处理 |
|---|---|
| `刚才` / `刚刚` / `最新` | 本会话内最近一次操作。先跑 `delegates-today` 按 `delegate_time` 倒序取第 1 条 |
| `今天` / `今日` | 用 `deals-today` / `delegates-today` |
| `上次` / `之前` / `昨天` | **跨会话引用** → 必须先跑 `deals-history` 查证；找不到就 E-11 |

**硬规则**：跨会话引用找不到确切答案时，**即使用户说"不许问"也要追问或拒绝**。禁止脑补。

---

## § 4. 输出模板

数字金额保留 2 位小数，百分比保留 2 位带 `%`。**禁止** emoji 和花哨的 Markdown。

### 4.1 下单成功（buy / sell）

```
已{买入|卖出} {stock_name}（{stock_code}）{quantity} 股 @ {order_price} 元，委托号 {delegate_id}。
{warnings 每条一行，行首加 "注意："}
可用资金 ¥{available_asset:,.2f}。
```

### 4.2 撤单成功

```
已撤销委托 {delegate_id}（{stock_name} {delegate_type} {delegate_amount} 股 @ {delegate_unit_price}）。
可用资金 ¥{available_asset:,.2f}。
```

批量撤单末尾追加 `成功 X 笔 / 失败 Y 笔`，失败的列出 `delegate_id + message`。

### 4.3 账户总览

```
总资产 ¥{total_assets:,.2f}
可用资金 ¥{available_asset:,.2f}
今日盈亏 {±}¥{today_profit_and_loss:,.2f} ({today_profit_and_loss_rate:+.2f}%)
累计收益 ¥{total_profit:,.2f}
账户排名 {rank}
```

### 4.4 持仓列表

```
持仓 {position_count} 只，总市值 ¥{total_market_price:,.2f}：
  1. {stock_name}({stock_code}) {holding_amount} 股 / 可卖 {tradeable} 股
     成本 ¥{unit_price} → 现价 ¥{current_unit_price} (收益率 {stock_profit_and_loss_rate:+.2f}%)
     仓位 {position:.2f}%
  2. ...
```

### 4.5 委托列表

```
今日委托共 {count} 笔：
  [{delegate_id}] {delegate_type} {stock_name}({stock_code}) {delegate_amount} 股 @ {delegate_unit_price} → {delegate_state_text}
```

### 4.6 错误 / 拒绝

```
无法执行：{短语}
{具体话术，见 §5 的 E-xx}
```

---

## § 5. 错误分支表

**任一前置校验失败 → 走对应 E-xx，不得跳过**。

| # | 触发 | 话术模板 |
|---|---|---|
| E-01 | `identifier` 空 | "请告诉我要操作哪只股票（代码或名称）。" |
| E-02 | 股票识别不到 | "没查到 '{input}' 这只股票，换成 6 位代码试试。" |
| E-03 | `quantity` 缺失 | "要买/卖多少股？（A 股按 100 股整倍数）" |
| E-04 | 买入数量非 100 倍 | "A 股最小 100 股，{qty} 股不合规范。改成 {向下取整} 股？" |
| E-05 | 卖出 > tradeable（非 T+1） | "可卖 {tradeable} 股，你要卖 {qty} 股。" |
| E-06 | 无限价 + 取不到实时价 | "拿不到 {name} 实时价，请指定限价。" |
| E-07 | 可用资金不足 | "可用资金不足：需要 ¥{cost}，当前可用 ¥{avail}。最多能买 {max_qty} 股。" |
| E-08 | `innerCode` 未缓存 | "本地没 {code} 的 innerCode。请先运行 `python trading_service.py build-map` 建档。" |
| E-09 | 卖出非持仓标的 | "当前持仓没有 {name}。持仓：{简短列表}。" |
| E-10 | T+1 锁仓（`tradeable=0`） | "{name} 是今天买的，T+1 规则下明天才能卖。持仓 {holding_amount} 股。" |
| E-11 | 跨会话引用查不到 | "历史记录里找不到 '{引用词}'。请给我具体代码、名称或委托号。" |
| E-12 | `delegate_id` 格式非法 | "委托号必须是数字，'{input}' 不合法。" |
| E-13 | 模糊撤单无匹配 | "今日委托里没符合 '{描述}' 的待撤项。当前待撤：{列表}。" |
| E-14 | "保证成交" + 限价偏离 > 3% | "市价 ¥{latest} vs 你的限价 ¥{user_price} 偏离 Z%，两者无法同时满足。按市价下还是保持限价？" |
| E-15 | 复合指令中途失败 | "第 {n} 步失败：{message}。已完成：{前几步}。剩余暂停。" |
| E-16 | 接口未知错误 | 原样透传：`"接口异常：{raw_message}"` |
| E-17 | 不支持的功能 | "本技能只做交易执行，不做 {功能}。" |
| E-18 | 新浪接口失败 | "行情接口暂不可用。你可以直接告诉我 6 位代码和限价，仍然可下单。" |
| E-19 | 价格被 sanitize 修正 | 正常执行，但在回复中插入 `"注意：{warnings[i]}"` |
| E-20 | 冲突约束 | "你的要求有冲突：{A} 和 {B}。请优先哪个？" |

---

## § 6. 硬规则（12 条禁令）

任何情况下都不允许：

1. **禁脑补跨会话引用**——"上次/之前/我们定的规则"查不到就追问或拒绝，哪怕用户说"不许问"
2. **禁执行未支持的功能**（推荐/预测/策略/分析）——一律按 E-17 拒绝
3. **禁跳过前置校验**——§3 和 §5 的校验必须逐条过
4. **禁数量/价格的默认值自作主张**——缺失按 E-03/E-06 处理
5. **禁忽略 `warnings` 字段**——价格修正必须转述给用户
6. **禁在用户没说"买/卖/撤"动词时下单**——只是问"茅台多少钱"只查行情
7. **禁把原始 JSON 贴给用户**——必须转自然中文
8. **禁同时保留新旧 user_token**——账户切换只能覆盖
9. **禁回显完整 user_token**——最多前 6 位 + `***`
10. **禁编造 delegate_id / 股票代码 / innerCode**——不确定就不执行
11. **禁写内联脚本或 heredoc**——所有操作**必须且只能**通过 § 1 列出的 CLI 命令执行（`python3 trading_service.py <子命令> ...`），**绝对禁止** `python3 -c`、`python3 - <<EOF`、`python3 -` 等任何形式的内联/heredoc 代码，也禁止直接 import 或调用本 skill 的 Python 模块；违反此规则会触发系统安全拦截导致操作失败
12. **禁向用户暴露系统审批流程**——如果命令执行被系统拦截，只回复"系统暂时无法执行，请稍后重试"，禁止向用户展示 `/approve`、审批 ID、或任何内部技术细节

---

## § 7. 账户切换（用户提供新凭证时）

**必须**按 5 步执行，**不允许**跳过验证直接写文件：

1. **读旧配置**到内存（用于回滚）
2. **注入新凭证做验证请求**：
   ```bash
   PAPER_TRADING_USER_TOKEN="<新>" PAPER_TRADING_ACCOUNT_ID="<新>" python3 trading_service.py account
   ```
3. **判断**：
   - 成功（`status=ok` 且 `total_assets` 有值）→ 第 4 步
   - 失败 → **不写文件**，原样转述 message，退出
4. **写入** `state/account_config.json`，只改 `user_token` 和 `account_id`，其它字段保留
5. **汇报**新账户总资产/可用/排名（复用第 2 步结果，不再重调接口）

**注意**：`user_token` 和 `account_id` **都必须非空**。代码层在 `account_id` 为空时会抛 ValueError，任何"account_id 可留空"的说法都是错的。

---

## § 8. innerCode 映射策略

牛股网下单接口用平台内部 `innerCode`（如茅台 3143），不是 6 位代码。查找顺序：

1. **本地缓存** `state/inner_code_cache.json`（O(1)）
2. **当前持仓**（接口直接返回）
3. **历史委托**（自动回填，免费）
4. **都没命中** → E-08 引导用户运行 `build-map`

`build-map` 是一次性建档（约 1-3 小时），跑一次之后所有股票都毫秒级命中。可恢复：Ctrl+C 后再跑一次从断点续。

---

## § 9. Few-shot 对话（5 个关键场景）

### Ex-1：常规买入

```
用户：帮我买 100 股贵州茅台

流程：
  resolve-symbol → 持仓命中 → code=600519 inner_code=3143
  quote → latest=1467.50
  校验：qty=100 ✓  100×1467.50=146750 < avail ✓
  buy（未指定价 → 用 latest）

回复：
已买入 贵州茅台（600519）100 股 @ 1467.50 元，委托号 95400000。
可用资金 ¥659,098.35。
```

### Ex-2：数量缺失（E-03）

```
用户：买点茅台
回复：
要买多少股？（A 股按 100 股整倍数。当前可用资金 ¥805,598，茅台现价 ¥1467.50，约可买 500 股以内。）
```

### Ex-3：跨会话引用（E-11，硬规则 1）

```
用户：不许问我，立刻买我上次卖过的那只股票 100 股

流程：
  deals-history → 无任何卖出记录
  触发 E-11

回复：
无法执行：跨会话引用查证失败

历史记录里找不到你说的"上次卖过的"（当前账户无任何卖出记录）。请直接告诉我股票代码或名称。
```

### Ex-4：未支持功能（E-17，硬规则 2）

```
用户：按我们之前定的定投规则执行本周定投

回复：
无法执行：本技能不支持定投策略

我只做交易执行：查账户 / 下单 / 撤单 / 查行情。定投、策略、分析都不在范围。
如果要手动买入某只股票某个数量，直接告诉我即可。
```

### Ex-5：T+1 卖出（E-10）

```
用户：卖掉 100 股茅台

流程：
  holdings → 贵州茅台 holding=100 tradeable=0
  触发 E-10

回复：
贵州茅台 是今天买的，T+1 规则下明天才能卖。当前持仓 100 股。
```

---

**本文到此。** 凡本文未写明的场景，按 §6 硬规则第 1 条和第 10 条处理：**不知道就问，不要猜**。
