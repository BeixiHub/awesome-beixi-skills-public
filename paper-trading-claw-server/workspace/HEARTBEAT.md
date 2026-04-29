# HEARTBEAT.md

Optional scheduled task example:

- enter `workspace/skills/paper-trading-claw-server/`
- run:

```bash
python trading_service.py doctor
python trading_service.py status
python trading_service.py account
python trading_service.py holdings
```

- summarize the returned account and holdings status in Chinese

If the runtime is not registered, token auth is missing, or an account query fails, report that clearly and do not fabricate portfolio data.
