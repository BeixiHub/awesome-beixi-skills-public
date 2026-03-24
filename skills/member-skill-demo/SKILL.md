# Member Skill Demo — OpenClaw Adapter

A stable local adapter for invoking the subscription-gated backend from OpenClaw.

## Contract

Send this payload to the backend:

```json
{
  "client": "openclaw",
  "response_mode": "compact",
  "license": "<license token>",
  "input": "<user request>"
}
```

## Usage

```powershell
python .\skills\member-skill-demo\openclaw_adapter.py --license "YOUR_LICENSE" --input "你的问题"
```

For a more organized handoff, use summary mode:

```powershell
python .\skills\member-skill-demo\openclaw_adapter.py --license "YOUR_LICENSE" --summary --input "今日A股能源板块有什么重要信号"
```

Or pipe JSON on stdin:

```powershell
@'
{"client":"openclaw","response_mode":"compact","license":"YOUR_LICENSE","input":"你的问题"}
'@ | python .\skills\member-skill-demo\openclaw_adapter.py
```

## Behavior

- Uses UTF-8 end-to-end.
- Reads license from CLI arg, env, or stdin JSON.
- Can request `compact`, `summary`, or `raw` response modes.
- Default is `summary`, so the backend response is auto-organized into a clean handoff.
- Auto-extracts common answer fields and prints a clean handoff.
- If the backend returns 403, reports membership check failure.
- Does not store the large prompt locally.
