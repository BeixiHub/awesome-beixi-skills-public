# 事件情报 MCP

这是事件情报工具的 MCP-only 版本，用于 Codex 或其他支持 MCP 的 agent。该包只保留面向 agent 的 MCP 工具入口，不包含 ArkClaw/OpenClaw 插件注册逻辑。

## 文件说明

- `SKILL.md`：agent 行为规则和回复边界。
- `mcp-server.mjs`：stdio MCP server 入口。
- `src/tools.cjs`：工具 schema、handler 和 Python runtime 桥接层。
- `scripts/`：MCP 工具调用的私有 Python runtime。

## 本地检查

```bash
npm install
npm run check
npm run list-tools
```

## MCP 启动命令

```bash
node /absolute/path/to/event-intelligence-mcp/mcp-server.mjs
```

请通过受支持的运行时配置或环境变量配置凭据和后端访问方式。不要把密钥写入 `SKILL.md` 或任何会被提交的文件。
