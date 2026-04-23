# 修正版接口说明

这份文档只记录已经确认过的真实行为，不照搬 Excel 原始写法。

## 核心结论

- 牛股网 V2 主凭证按 `ClawToken` 使用
- 旧 `userToken` 不是这套 V2 的主入口
- 注册接口是 `POST + application/json`
- 注册接口里的 `mobile` 必须先 AES-CBC 加密，再 Base64
- `GetAccount` 会返回 `accountId`
- `GetStockHoldingList` 当前实测不依赖 `accountId`
- `AddDelegate` 不能只看 `success`
- `CancelDelegate` 当前可能返回上游异常

## 注册接口

```text
POST /virtualtrade/ClawUser/Register
```

请求头：

```text
Content-Type: application/json
```

请求体：

```json
{
  "clawToken": "pt_xxx",
  "mobile": "<AES_BASE64>"
}
```

注意：

- 明文手机号会失败
- `form-data` 会失败
- 现在 skill 已内置 AES 处理，可以直接本地注册

## 查询接口

### 账户

```text
GET /virtualtrade/Race/GetAccount?ClawToken=...
```

常见字段：

- `accountId`
- `userName`
- `totalAssets`
- `availableAsset`
- `rank`

### 持仓

```text
GET /virtualtrade/Race/GetStockHoldingList?ClawToken=...
```

实测结论：

- 只传 `ClawToken` 就能成功
- `accountId` 当前不是必填

### 今日成交

```text
GET /virtualtrade/Race/GetTodayList?ClawToken=...
```

### 历史成交

```text
GET /virtualtrade/Race/GetHistoryList?ClawToken=...
```

### 今日委托

```text
GET /virtualtrade/Race/GetTodayDelegateList?ClawToken=...
```

### 历史委托

```text
GET /virtualtrade/Race/GetHistoryDelegateList?ClawToken=...
```

## 交易接口

### 下单

```text
GET /virtualtrade/Race/AddDelegate
```

参数：

- `ClawToken`
- `innerCode`
- `amount`
- `price`
- `type`

其中：

- `type = 1` 表示买入
- `type = 2` 表示卖出

结果解释规则：

- `code = 0` 且 `message` 包含“委托成功”或“资金冻结”，按成功处理
- `success = false` 也不代表一定失败
- 最稳妥的办法是再查一次 `GetTodayDelegateList`

### 撤单

```text
GET /virtualtrade/Race/CancelDelegate
```

参数：

- `ClawToken`
- `id`

当前已见真实异常：

```json
{
  "success": false,
  "code": -1,
  "message": "获取股票数据异常"
}
```

所以撤单必须保守解释：

- 没确认，就不要说成功
