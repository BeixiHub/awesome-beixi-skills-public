# analysis-consultant

This folder is the installed analysis-consultant package.

## Contents
- `SKILL.md` - the skill instructions that the agent reads.
- `analysis_consultant.py` - user-facing wrapper entrypoint.
- `openclaw_adapter.py` - stable backend adapter.

## How to use
1. Keep the folder in the agent skills directory.
2. Run `python analysis_consultant.py --license "eyJ2IjoxLCJwcm9kdWN0IjoibWVtYmVyLXNraWxsLWRlbW8iLCJlbWFpbCI6ImFub255bW91cyIsInBsYW4iOiJzdGFuZGFyZCIsIm9yZGVyX2lkIjoib3JkXzUxMDk4OTgwX2UzYjhjMCIsInNvdXJjZSI6ImRlbW9fcHVyY2hhc2VfcGFnZSIsImlhdCI6MTc3NDMzOTczNSwiZXhwIjoxNzg5ODkxNzM1LCJub25jZSI6Ijc0ZDU1OWU0ZjFhZmYyNjhhY2ZiMmRhZSJ9.FPIR_TIoowmTD6OpdIcpICQdr41CtraAjhFAp6SydFc" --input "你的问题"`.
3. Or set `MEMBER_SKILL_LICENSE` and pass the input through stdin or `--input`.

## License details
- Buyer: anonymous
- Plan: standard
- Expires: 2026-09-20T08:08:55.000Z
- Install into: C:\Users\hanji\.openclaw\workspace\skills\analysis-consultant