# paper-trading-claw (OpenClaw Skill)

本项目将 `paper-trading-claw` skill 打包为 `arkClawDemo` workspace 格式。

这是一个 A 股 / ETF 模拟盘交易执行 skill，支持：
- 模拟账户注册
- 账户 / 持仓 / 成交 / 委托查询
- 行情查询与股票代码解析
- 买入 / 卖出 / 撤单操作

核心实现在 [workspace/skills/paper-trading-claw](workspace/skills/paper-trading-claw) 目录下。

## 目录结构

- `workspace/AGENTS.md`：路由与 workspace 规则
- `workspace/SOUL.md`：助手角色与回复风格
- `workspace/TOOLS.md`：运行时配置说明
- `workspace/routing-rules.json`：触发词 -> skill 映射
- `workspace/skills/paper-trading-claw/SKILL.md`：主 skill 规格说明
- `workspace/skills/paper-trading-claw/trading_service.py`：CLI 入口
- `workspace/skills/paper-trading-claw/scripts/`：运行时模块
- `workspace/skills/paper-trading-claw/references/`：参考文档
- `workspace/skills/paper-trading-claw/state/`：本地运行时状态

## 调用方式

在 skill 目录下运行命令：

```bash
cd workspace/skills/paper-trading-claw
python trading_service.py <command> ...
```

示例：

```bash
python trading_service.py doctor
python trading_service.py status
python trading_service.py register --phone "13800138000" --save-state
python trading_service.py register --real-name "张三" --phone "13800138000" --save-state
python trading_service.py account
python trading_service.py holdings
python trading_service.py quote "贵州茅台"
python trading_service.py buy "600519" 100
python trading_service.py cancel 95449276
```

## 配置

本地直连模式使用内置默认值，通常不需要额外配置密钥文件。

可选环境变量：

- `PAPER_TRADING_NGW_BASE_URL`
- `PAPER_TRADING_NGW_AES_KEY`
- `PAPER_TRADING_NGW_AES_IV`
- `PAPER_TRADING_REQUEST_TIMEOUT_SECONDS`
- `PAPER_TRADING_BACKEND_BASE_URL`
- `PAPER_TRADING_BACKEND_SECRET`

以 `.env.example` 为模板，不要提交真实的 `.env` 文件。

## 发布注意事项

- 发布前清空 `state/binding_state.json`
- 发布前清空 `state/inner_code_cache.json`
- 不要提交 `.env`
- 不要提交本地运行日志或缓存
