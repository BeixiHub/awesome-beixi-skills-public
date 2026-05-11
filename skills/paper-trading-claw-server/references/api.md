# API Contract

当前转接服务地址：`http://42.193.103.122:10288/admin-api`

所有业务接口走平台转接路径：

- `/paper-trading/api/v1/...`

请求必须带平台租户 header：

```http
tenant-id: 1
```

转接服务返回平台统一包装：

```json
{
  "code": 0,
  "msg": "",
  "data": {}
}
```

客户端脚本会自动拆开该包装，并把 `data` 中的上游原始 JSON 输出给智能体。失败时，脚本会把 `[CANCEL_DISABLED] ...` 这类平台错误消息还原成原来的 `success=false/code/message` 结构。

## 注册绑定流程

注册链路接口会强制返回 `mandatoryNotice` 字段，包括 `/sms/send`、`/sms/verify`、`/register`。客户端或智能体必须把 `mandatoryNotice.text` 原样展示给用户。

短信验证码有效期为 `300` 秒，也就是 5 分钟。

发送短信：

```http
POST /paper-trading/api/v1/sms/send
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
POST /paper-trading/api/v1/sms/verify
{
  "userId": "USER_001",
  "clawToken": "pt_xxx",
  "phone": "13800138000",
  "code": "123456"
}
```

注册牛股王模拟盘：

```http
POST /paper-trading/api/v1/register
{
  "userId": "USER_001",
  "clawToken": "pt_xxx"
}
```

查询状态：

```http
GET /paper-trading/api/v1/status?clawToken=pt_xxx
```

## 查询接口

```http
GET /paper-trading/api/v1/account
GET /paper-trading/api/v1/holdings
GET /paper-trading/api/v1/deals/today
GET /paper-trading/api/v1/deals/history
GET /paper-trading/api/v1/delegates/today
GET /paper-trading/api/v1/delegates/history
GET /paper-trading/api/v1/usage/report
```

## 行情和 innerCode

```http
POST /paper-trading/api/v1/market/resolve-symbol
{ "identifier": "神州高铁" }

POST /paper-trading/api/v1/market/quote
{ "identifier": "000008" }

POST /paper-trading/api/v1/inner-code/resolve
{ "userId": "USER_001", "clawToken": "pt_xxx", "identifier": "000008" }

POST /paper-trading/api/v1/inner-code/seed
{ "userId": "USER_001", "clawToken": "pt_xxx", "stockCode": "000008", "stockName": "神州高铁", "innerCode": 8 }
```

## 交易接口

买入：

```http
POST /paper-trading/api/v1/orders/buy
{
  "userId": "USER_001",
  "clawToken": "pt_xxx",
  "identifier": "000008",
  "quantity": 100,
  "price": 2.50,
  "innerCode": 8
}
```

卖出：

```http
POST /paper-trading/api/v1/orders/sell
{
  "userId": "USER_001",
  "clawToken": "pt_xxx",
  "identifier": "000008",
  "quantity": 100,
  "price": 2.60,
  "innerCode": 8
}
```

撤单功能已下线。旧路径保留用于兼容，但固定返回 `410 CANCEL_DISABLED`，不会再调用牛股王撤单接口。

```http
POST /paper-trading/api/v1/orders/cancel
{ "delegateId": 10001 }
```
