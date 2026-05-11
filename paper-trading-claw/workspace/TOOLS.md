# TOOLS.md

This workspace uses the skill at `skills/paper-trading-claw/`.

## Entry command

Run from the skill directory:

```powershell
cd .\workspace\skills\paper-trading-claw
python .\trading_service.py <command> ...
```

## Runtime files

This workspace does not keep user binding state locally. User binding data, SMS sessions, innerCode cache, and audit records are stored by the remote paper-trading transfer service.

Runtime artifacts such as `.env`, logs, and Python caches should stay local.

## Environment variables

Transfer-service mode:
- `PAPER_TRADING_API_BASE_URL`
- `PAPER_TRADING_TENANT_ID`
- `PAPER_TRADING_API_TOKEN` optional; current transfer service does not require bearer auth
- `PAPER_TRADING_USER_ID`
- `PAPER_TRADING_API_TIMEOUT_SECONDS`

`.env.example` is the template. A real `.env` must remain local only.
