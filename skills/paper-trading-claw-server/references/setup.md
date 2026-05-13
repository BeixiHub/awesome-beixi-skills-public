# Setup

## 当前文件夹用途

这个文件夹是给另一个智能体本地调用的客户端 skill，只负责访问远端 API，不包含后端服务、短信密钥、牛股王直连代码或用户数据库。

## 远端转接服务

当前环境：

```text
http://42.193.103.122:10288/admin-api
```

请求需要平台租户 header 和开放平台 API Key。客户端通过 `PAPER_TRADING_TENANT_ID=1` 自动添加 `tenant-id: 1`，并通过 `DEEPSEEK_DATA_API_KEY` 自动添加 `X-API-Key`。当前转接服务不使用 bearer token 鉴权。

## skill 客户端

1. 进入本目录
2. 安装依赖：`pip install -r requirements.txt`
3. 复制 `.env.example` 为 `.env`
4. 填写 `DEEPSEEK_DATA_API_KEY`，或把根目录 `router_env` 放在 skill 的父级路径中让客户端自动读取
5. 确认 `PAPER_TRADING_API_BASE_URL` 和 `PAPER_TRADING_TENANT_ID`
6. 运行：`python trading_service.py doctor`

平台集成时，优先用真实业务用户 ID 设置 `PAPER_TRADING_USER_ID` 或传 `--user-id`。
