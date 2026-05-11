# TOOLS.md

This workspace uses the skill at `skills/paper-trading-claw-server/`.

## Entry command

Run from the skill directory:

```powershell
cd .\workspace\skills\paper-trading-claw-server
python .\trading_service.py <command> ...
```

## Runtime model

The skill is a client for the remote paper-trading transfer-service API. User bindings, SMS sessions, innerCode cache, and audit logs are stored by the server, not inside this workspace.

## Environment variables

Common runtime variables:
- `PAPER_TRADING_API_BASE_URL`
- `PAPER_TRADING_TENANT_ID`
- `PAPER_TRADING_API_TOKEN` optional; current transfer service does not require bearer auth
- `PAPER_TRADING_USER_ID`
- `PAPER_TRADING_API_TIMEOUT_SECONDS`

`.env.example` is the template. A real `.env` must remain local only.
