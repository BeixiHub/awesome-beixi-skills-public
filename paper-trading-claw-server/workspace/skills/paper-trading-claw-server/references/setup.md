# Setup

## 当前文件夹用途

这个文件夹是给另一个智能体本地调用的客户端 skill，只负责访问远端 API，不包含后端服务、短信密钥、牛股王直连代码或用户数据库。

## 远端后端

当前测试环境：

```text
http://42.193.103.122:7085
```

业务接口需要 `PAPER_TRADING_API_TOKEN`。不要把真实 token 写进 `SKILL.md`、`.env.example` 或提交到仓库；只放在部署环境变量或本地 `.env`。

## skill 客户端

1. 进入本目录
2. 安装依赖：`pip install -r requirements.txt`
3. 复制 `.env.example` 为 `.env`
4. 填写 `PAPER_TRADING_API_TOKEN`
5. 运行：`python trading_service.py doctor`

平台集成时，优先用真实业务用户 ID 设置 `PAPER_TRADING_USER_ID` 或传 `--user-id`。
