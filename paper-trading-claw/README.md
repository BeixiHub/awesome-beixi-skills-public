# paper-trading-claw (OpenClaw Skill)

This demo packages the `paper-trading-claw` skill into the `arkClawDemo` workspace format.

It is an A-share / ETF paper-trading execution skill for:
- simulated account registration
- account / holdings / deals / delegates queries
- quote and symbol resolution
- virtual buy / sell operations
- disabled-cancel guidance through delegate/deal status queries

The implementation lives in [workspace/skills/paper-trading-claw](C:/Users/hanji/Downloads/Beixi/arkClawDemo/paper-trading-claw/workspace/skills/paper-trading-claw).

## Workspace layout

- `workspace/AGENTS.md`: routing and workspace rules
- `workspace/SOUL.md`: assistant role and response style
- `workspace/TOOLS.md`: runtime configuration notes
- `workspace/routing-rules.json`: trigger -> skill mapping
- `workspace/skills/paper-trading-claw/SKILL.md`: primary skill spec
- `workspace/skills/paper-trading-claw/trading_service.py`: OpenClaw-friendly CLI entry
- `workspace/skills/paper-trading-claw/scripts/`: runtime modules
- `workspace/skills/paper-trading-claw/references/`: supporting docs

All user binding data, SMS sessions, innerCode cache, and audit records live on the remote transfer service, not in this workspace.

## OpenClaw invocation

Run commands inside the skill directory:

```powershell
cd .\workspace\skills\paper-trading-claw
python .\trading_service.py <command> ...
```

Examples:

```bash
python trading_service.py doctor
python trading_service.py status
python trading_service.py send-sms --user-id USER_001 --phone "13800138000" --real-name "张三"
python trading_service.py verify-code --user-id USER_001 --claw-token pt_xxx --phone "13800138000" --code "123456"
python trading_service.py register --user-id USER_001 --claw-token pt_xxx
python trading_service.py account
python trading_service.py holdings
python trading_service.py quote "贵州茅台"
python trading_service.py buy "600519" --quantity 100 --claw-token pt_xxx
python trading_service.py delegates-today
```

## Configuration

This package is a thin client. It calls the remote paper-trading transfer service:

```text
http://42.193.103.122:10288/admin-api
```

Environment variables:

- `PAPER_TRADING_API_BASE_URL`
- `PAPER_TRADING_TENANT_ID`
- `PAPER_TRADING_API_TOKEN` optional; current transfer service does not require bearer auth
- `PAPER_TRADING_USER_ID`
- `PAPER_TRADING_API_TIMEOUT_SECONDS`

Use `.env.example` as the template only. Do not commit a real `.env`.

## Release hygiene

- do not commit `.env`
- do not commit local runtime logs or caches
- do not reintroduce direct Niuguwang or SMS-provider credentials into this workspace
