# HEARTBEAT.md

Optional scheduled task example:

- enter `workspace/skills/paper-trading-claw/`
- run:

```bash
python trading_service.py account
python trading_service.py holdings
```

- summarize the returned account and holdings status in Chinese

If the runtime is not registered or the account query fails, report that clearly and do not fabricate portfolio data.
