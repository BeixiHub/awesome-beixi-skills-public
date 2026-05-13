# Error Handling

客户端输出给智能体的是拆开平台包装后的上游 JSON。成功响应包含：

```json
{
  "success": true,
  "operation": "status",
  "message": "...",
  "data": {}
}
```

转接服务原始响应是 `{ "code": 0, "msg": "", "data": {} }`，客户端会自动取 `data`。失败时，客户端会把平台错误还原成：

```json
{
  "success": false,
  "code": "SMS_NOT_VERIFIED",
  "message": "...",
  "data": {},
  "nextAction": "..."
}
```

常见错误：

- `PLATFORM_1010000010` / `API Key 不存在`: 没有配置 `DEEPSEEK_DATA_API_KEY`，或请求没有带 `X-API-Key`。
- `UNAUTHORIZED`: 兼容旧 bearer-token 后端时可能出现；当前转接服务主要使用 `DEEPSEEK_DATA_API_KEY`。
- `INVALID_PHONE`: 手机号格式不正确。
- `SMS_VERIFY_FAILED`: 火山短信验证码校验失败。
- `SMS_NOT_VERIFIED`: 还没完成短信验证，不能注册。
- `REGISTRATION_REQUIRED`: 还没完成注册绑定，不能查询交易账户或下单。
- `INNER_CODE_REQUIRED`: 后端无法自动找到牛股王 `innerCode`，需要用户提供或先查持仓/委托回填。
- `QUOTE_REQUIRED`: 没有可用行情，需要用户给出限价。
- `NIUGUWANG_QUERY_FAILED`: 牛股王查询接口返回失败。
- `NIUGUWANG_REGISTER_FAILED`: 牛股王注册接口返回失败。
- `CANCEL_DISABLED`: 撤单功能已下线；改用今日委托和今日成交查询确认订单状态。
