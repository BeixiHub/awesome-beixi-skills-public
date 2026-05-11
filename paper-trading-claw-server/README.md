# paper-trading-claw-server (OpenClaw Skill)

This demo packages the `paper-trading-claw-server` skill into the `arkClawDemo` workspace format.

It is an A-share / ETF paper-trading client skill for:
- SMS verification and simulated account registration
- account / holdings / deals / delegates queries
- quote and stock symbol / innerCode resolution
- virtual buy / sell operations through the Beixi transfer-service API

Cancel order execution is disabled. Use today's delegates and deals queries to inspect order status.

The implementation lives in `workspace/skills/paper-trading-claw-server`.

## Workspace layout

- `workspace/AGENTS.md`: routing and workspace rules
- `workspace/SOUL.md`: assistant role and response style
- `workspace/TOOLS.md`: runtime configuration notes
- `workspace/routing-rules.json`: trigger -> skill mapping
- `workspace/skills/paper-trading-claw-server/SKILL.md`: primary skill spec
- `workspace/skills/paper-trading-claw-server/trading_service.py`: OpenClaw-friendly CLI entry
- `workspace/skills/paper-trading-claw-server/scripts/`: API client runtime modules
- `workspace/skills/paper-trading-claw-server/references/`: setup, API, and error handling docs

## OpenClaw invocation

Run commands inside the skill directory:

```powershell
cd .\workspace\skills\paper-trading-claw-server
python .\trading_service.py <command> ...
```

Examples:

```bash
python trading_service.py doctor
python trading_service.py send-sms --phone "13800138000" --real-name "张三" --user-id "USER_001"
python trading_service.py verify-code --phone "13800138000" --code "123456" --user-id "USER_001" --claw-token "pt_xxx"
python trading_service.py register --user-id "USER_001" --claw-token "pt_xxx"
python trading_service.py account --user-id "USER_001"
python trading_service.py holdings --user-id "USER_001"
python trading_service.py quote "000008" --user-id "USER_001"
python trading_service.py buy "000008" --quantity 100 --price 2.50 --inner-code 8 --user-id "USER_001" --claw-token "pt_xxx"
```

## Configuration

Copy `.env.example` to `.env` inside the skill directory. The current transfer service uses:

```text
http://42.193.103.122:10288/admin-api
```

Common settings:
- `PAPER_TRADING_API_BASE_URL`
- `PAPER_TRADING_TENANT_ID`
- `PAPER_TRADING_API_TOKEN` optional; current transfer service does not require bearer auth
- `PAPER_TRADING_USER_ID`
- `PAPER_TRADING_API_TIMEOUT_SECONDS`

Platform integrations should pass a real business user ID instead of sharing the default `local-user`.

## Release hygiene

- do not commit a real `.env`
- do not commit local runtime logs, SQLite files, tokens, phone numbers, or verification codes
- keep `.env.example` as the only committed environment template
