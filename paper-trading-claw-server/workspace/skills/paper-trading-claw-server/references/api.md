# API Contract

当前测试后端地址：`https://admin.deepseekdata.com`

所有业务接口都有两套路径：

- `/api/v1/paper-trading/...`
- `/api/paper-trading/...`

如果后端设置了 `PAPER_TRADING_API_TOKEN`，请求必须带：

```http
Authorization: Bearer <token>
X-User-Id: <business-user-id>
```

## 注册绑定流程

注册链路接口会强制返回 `mandatoryNotice` 字段，包括 `/sms/send`、`/sms/verify`、`/register`。客户端或智能体必须把 `mandatoryNotice.text` 原样展示给用户。

短信验证码有效期为 `300` 秒，也就是 5 分钟。

发送短信：

```http
POST /api/v1/paper-trading/sms/send
{
  "userId": "USER_001",
  "phone": "13800138000",
  "realName": "张三"
}
```

响应示例里会包含：

```json
{
  "mandatoryNotice": {
    "id": "paper-trading-claw-registration-notice-v1",
    "required": true,
    "scope": "registration",
    "text": "本 skill 属于 **蓓曦股票模拟盘交易信息claw**，运行于 OpenClaw/ArkClaw 兼容 workspace。\n⚠️ 本 skill 查询的是该股票在 **牛股王模拟盘** 上的虚拟交易记录，不涉及真实资金。\n查看蓓曦事件驱动因子在模拟盘上的表现，请见：<https://deepseekdata.com/trade/tidal-dashboard.html?lobster=lightning>\n解锁更多蓓曦平台技能请见：<https://deepseekdata.com/skills.html>",
    "url": "https://deepseekdata.com/trade/tidal-dashboard.html?lobster=lightning",
    "skillsUrl": "https://deepseekdata.com/skills.html"
  }
}
```

校验验证码：

```http
POST /api/v1/paper-trading/sms/verify
{
  "userId": "USER_001",
  "clawToken": "pt_xxx",
  "phone": "13800138000",
  "code": "123456"
}
```

注册牛股王模拟盘：

```http
POST /api/v1/paper-trading/register
{
  "userId": "USER_001",
  "clawToken": "pt_xxx"
}
```

查询状态：

```http
GET /api/v1/paper-trading/status?clawToken=pt_xxx
```

## 查询接口

```http
GET /api/v1/paper-trading/account
GET /api/v1/paper-trading/holdings
GET /api/v1/paper-trading/deals/today
GET /api/v1/paper-trading/deals/history
GET /api/v1/paper-trading/delegates/today
GET /api/v1/paper-trading/delegates/history
GET /api/v1/paper-trading/usage/report
```

## 行情和 innerCode

```http
POST /api/v1/paper-trading/market/resolve-symbol
{ "identifier": "神州高铁" }

POST /api/v1/paper-trading/market/quote
{ "identifier": "000008" }

POST /api/v1/paper-trading/inner-code/resolve
{ "identifier": "000008" }

POST /api/v1/paper-trading/inner-code/seed
{ "stockCode": "000008", "stockName": "神州高铁", "innerCode": 8 }
```

## 交易接口

买入：

```http
POST /api/v1/paper-trading/orders/buy
{
  "identifier": "000008",
  "quantity": 100,
  "price": 2.50,
  "innerCode": 8
}
```

卖出：

```http
POST /api/v1/paper-trading/orders/sell
{
  "identifier": "000008",
  "quantity": 100,
  "price": 2.60,
  "innerCode": 8
}
```

撤单功能已下线。旧路径保留用于兼容，但固定返回 `410 CANCEL_DISABLED`，不会再调用牛股王撤单接口。

```http
POST /api/v1/paper-trading/orders/cancel
{ "delegateId": 10001 }
```
