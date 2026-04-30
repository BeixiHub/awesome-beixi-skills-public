# paper-trading-claw-server（OpenClaw Skill）

本 demo 将 `paper-trading-claw-server` skill 打包为 `arkClawDemo` 工作区格式。

这是一个 A 股 / ETF 模拟交易客户端 skill，支持以下功能：
- 短信验证与模拟账户注册
- 账户 / 持仓 / 成交 / 委托查询
- 行情查询与股票代码 / innerCode 解析
- 通过蓓曦后端 API 执行虚拟买入 / 卖出操作

撤单功能已禁用。请使用 `delegates-today` 和 `deals-today` 命令查询当日委托和成交状态。

实现代码位于 `workspace/skills/paper-trading-claw-server`。

## 工作区目录结构

- `workspace/AGENTS.md`：路由与工作区规则
- `workspace/SOUL.md`：助手角色与回复风格
- `workspace/TOOLS.md`：运行时配置说明
- `workspace/routing-rules.json`：触发器 -> skill 映射
- `workspace/skills/paper-trading-claw-server/SKILL.md`：主要 skill 规格说明
- `workspace/skills/paper-trading-claw-server/trading_service.py`：OpenClaw 兼容的 CLI 入口
- `workspace/skills/paper-trading-claw-server/scripts/`：API 客户端运行时模块
- `workspace/skills/paper-trading-claw-server/references/`：环境配置、API 和错误处理文档

## OpenClaw 调用方式

在 skill 目录下运行命令：

```powershell
cd .\workspace\skills\paper-trading-claw-server
python .\trading_service.py <command> ...
```

示例：

```bash
python trading_service.py doctor
python trading_service.py send-sms --phone "13800138000" --real-name "张三" --user-id "USER_001"
python trading_service.py verify-code --phone "13800138000" --code "123456" --user-id "USER_001" --claw-token "pt_xxx"
python trading_service.py register --user-id "USER_001" --claw-token "pt_xxx"
python trading_service.py account --user-id "USER_001"
python trading_service.py holdings --user-id "USER_001"
python trading_service.py quote "000008" --user-id "USER_001"
python trading_service.py buy "000008" --quantity 100 --price 2.50 --inner-code 8 --user-id "USER_001"
```

## 配置

将 skill 目录下的 `.env.example` 复制为 `.env`，并填入平台提供的 API Token。

后端认证启用时必填：
- `PAPER_TRADING_API_TOKEN`

常用配置项：
- `PAPER_TRADING_API_BASE_URL`
- `PAPER_TRADING_USER_ID`
- `PAPER_TRADING_API_TIMEOUT_SECONDS`

平台集成时应传入真实的业务用户 ID，而非使用默认的 `local-user`。

## 发布注意事项

- 不要提交真实的 `.env` 文件
- 不要提交本地运行日志、SQLite 文件、Token、手机号或验证码
- 仅保留 `.env.example` 作为唯一提交的环境变量模板
