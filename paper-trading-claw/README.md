# paper-trading-claw (OpenClaw Skill)

This demo packages the `paper-trading-claw` skill into the `arkClawDemo` workspace format.

It is an A-share / ETF paper-trading execution skill for:
- simulated account registration
- account / holdings / deals / delegates queries
- quote and symbol resolution
- buy / sell / cancel operations

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
- `workspace/skills/paper-trading-claw/state/`: local runtime state templates

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
python trading_service.py register --phone "13800138000" --save-state
python trading_service.py register --real-name "张三" --phone "13800138000" --save-state
python trading_service.py account
python trading_service.py holdings
python trading_service.py quote "贵州茅台"
python trading_service.py buy "600519" 100
python trading_service.py cancel 95449276
```

## Configuration

Direct local mode works with built-in defaults and usually does not require any local secret file.

Optional environment variables:

- `PAPER_TRADING_NGW_BASE_URL`
- `PAPER_TRADING_NGW_AES_KEY`
- `PAPER_TRADING_NGW_AES_IV`
- `PAPER_TRADING_REQUEST_TIMEOUT_SECONDS`
- `PAPER_TRADING_BACKEND_BASE_URL`
- `PAPER_TRADING_BACKEND_SECRET`

Use `.env.example` as the template only. Do not commit a real `.env`.

## Release hygiene

- keep `state/binding_state.json` empty before publishing
- keep `state/inner_code_cache.json` empty before publishing
- do not commit `.env`
- do not commit local runtime logs or caches
