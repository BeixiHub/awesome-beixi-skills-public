# Analysis consultant — OpenClaw Adapter

A stable local adapter for invoking the analysis backend from OpenClaw.

It is meant to be used as an Analysis consultant: the backend response is normalized and handed back in a clean, readable form.

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
python .\skills\analysis-consultant\openclaw_adapter.py --license "eyJ2IjoxLCJwcm9kdWN0IjoibWVtYmVyLXNraWxsLWRlbW8iLCJlbWFpbCI6ImFub255bW91cyIsInBsYW4iOiJzdGFuZGFyZCIsIm9yZGVyX2lkIjoib3JkXzUxMDk4OTgwX2UzYjhjMCIsInNvdXJjZSI6ImRlbW9fcHVyY2hhc2VfcGFnZSIsImlhdCI6MTc3NDMzOTczNSwiZXhwIjoxNzg5ODkxNzM1LCJub25jZSI6Ijc0ZDU1OWU0ZjFhZmYyNjhhY2ZiMmRhZSJ9.FPIR_TIoowmTD6OpdIcpICQdr41CtraAjhFAp6SydFc" --input "你的问题"
```

For a more organized handoff, use summary mode:

```powershell
python .\skills\analysis-consultant\openclaw_adapter.py --license "eyJ2IjoxLCJwcm9kdWN0IjoibWVtYmVyLXNraWxsLWRlbW8iLCJlbWFpbCI6ImFub255bW91cyIsInBsYW4iOiJzdGFuZGFyZCIsIm9yZGVyX2lkIjoib3JkXzUxMDk4OTgwX2UzYjhjMCIsInNvdXJjZSI6ImRlbW9fcHVyY2hhc2VfcGFnZSIsImlhdCI6MTc3NDMzOTczNSwiZXhwIjoxNzg5ODkxNzM1LCJub25jZSI6Ijc0ZDU1OWU0ZjFhZmYyNjhhY2ZiMmRhZSJ9.FPIR_TIoowmTD6OpdIcpICQdr41CtraAjhFAp6SydFc" --summary --input "今日A股能源板块有什么重要信号"
```

Or pipe JSON on stdin:

```powershell
@'
{"client":"openclaw","response_mode":"compact","license":"eyJ2IjoxLCJwcm9kdWN0IjoibWVtYmVyLXNraWxsLWRlbW8iLCJlbWFpbCI6ImFub255bW91cyIsInBsYW4iOiJzdGFuZGFyZCIsIm9yZGVyX2lkIjoib3JkXzUxMDk4OTgwX2UzYjhjMCIsInNvdXJjZSI6ImRlbW9fcHVyY2hhc2VfcGFnZSIsImlhdCI6MTc3NDMzOTczNSwiZXhwIjoxNzg5ODkxNzM1LCJub25jZSI6Ijc0ZDU1OWU0ZjFhZmYyNjhhY2ZiMmRhZSJ9.FPIR_TIoowmTD6OpdIcpICQdr41CtraAjhFAp6SydFc","input":"你的问题"}
'@ | python .\skills\analysis-consultant\openclaw_adapter.py
```

## Behavior

- Uses UTF-8 end-to-end.
- Reads license from CLI arg, env, or stdin JSON.
- Can request `compact`, `summary`, `rich`, or `raw` response modes.
- Default is `rich`, so the backend response is more detailed and suitable for handing to the user.
- Auto-extracts common answer fields and prints a clean handoff.
- If the backend returns 403, reports membership check failure.
- Does not store the large prompt locally.

## License details

- Buyer: anonymous
- Plan: standard
- Expires: 2026-09-20T08:08:55.000Z
- Install into: C:\Users\hanji\.openclaw\workspace\skills\analysis-consultant

## Local demo notes
- In local testing, you may send token: "demo-123" instead of license.
- For OpenClaw, prefer client: "openclaw" and response_mode: "compact" to keep replies short and stable.