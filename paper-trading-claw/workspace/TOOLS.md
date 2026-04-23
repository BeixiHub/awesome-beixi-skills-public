# TOOLS.md

This workspace uses the skill at `skills/paper-trading-claw/`.

## Entry command

Run from the skill directory:

```powershell
cd .\workspace\skills\paper-trading-claw
python .\trading_service.py <command> ...
```

## Runtime files

Local runtime state lives under:

- `skills/paper-trading-claw/state/binding_state.json`
- `skills/paper-trading-claw/state/inner_code_cache.json`

These are local runtime artifacts and should stay out of published output.

## Environment variables

Direct local mode:
- `PAPER_TRADING_NGW_BASE_URL`
- `PAPER_TRADING_NGW_AES_KEY`
- `PAPER_TRADING_NGW_AES_IV`
- `PAPER_TRADING_REQUEST_TIMEOUT_SECONDS`

Backend mode only:
- `PAPER_TRADING_BACKEND_BASE_URL`
- `PAPER_TRADING_BACKEND_SECRET`

`.env.example` is the template. A real `.env` must remain local only.
