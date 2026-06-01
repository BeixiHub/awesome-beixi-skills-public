---
name: event-intelligence
description: 定时推送、手动获取和查询 deepseekdata 平台事件；定时任务使用 OpenClaw cron。
---

# Event Intelligence

## Fast Path For Agents

When the user asks to fetch, push, stream, schedule, or inspect recent platform events, run the matching runtime command first. Keep user-facing output short: show the result and a brief source/time-window note only. Do not paste raw JSON, progress logs, implementation steps, scheduling internals, or suggestions unless the user explicitly asks. If the API result is empty, follow the Empty API Result rule below.

## User-Facing Language Boundary

The end user is a professional investor reading messages in WeChat. They are usually not a software engineer. Internal technical terms are allowed in this skill file so the LLM can execute correctly, but they must not leak into normal WeChat replies.

Internal-only terms include: `API`, `API key`, `token`, `webhook`, `accountId`, `OpenClaw`, `cron`, `CLI`, `runtime`, `config`, `JSON`, `NO_REPLY`, `push_runtime.py`, `state/`, `eventId`, `detail-from-ref`, `--openclaw-output`, environment variables, file paths, command names, stack traces, and raw status fields.

Do not say these internal-only terms to the user unless the user explicitly asks for technical implementation details. Translate them into plain investor-facing language:

- `API/platform query` -> `平台事件库`
- `NO_REPLY/empty result` -> `本次平台事件库未命中`
- `configured/missing` -> `已设置` / `尚未设置`
- `cron/schedule/runtime` -> `定时推送`
- `eventId/detail-from-ref/history` -> `这条事件` / `上一条推送`

Normal WeChat output should look like investment intelligence, not diagnostics. Prefer: `结果：...` `来源：平台事件库` `时间范围：...` `说明：...`. Avoid explaining how commands, config, credentials, scheduling, filtering, or history lookup work.

Manual fetch in the current conversation:

```bash
python push_runtime.py manual-push --minutes <MINUTES> --keywords "<KEYWORD>" --no-delivery
```

For an explicit no-keyword manual fetch, pass an empty keyword string and optional structured filters:

```bash
python push_runtime.py manual-push --minutes <MINUTES> --keywords "" --event-source "<SOURCE>" --event-type "<TYPE>" --no-delivery
```

Structured full-list fetch without keyword:

```bash
python push_runtime.py list-events --start "YYYY-MM-DD HH:MM:SS" --end "YYYY-MM-DD HH:MM:SS" --event-source "<SOURCE>" --event-type "<TYPE>" --no-delivery
```

Install scheduled pushes through OpenClaw cron:

```bash
python push_runtime.py configure --active --keywords "<KEYWORD1>,<KEYWORD2>" --schedule 5m --page-size 10
python push_runtime.py install-schedule
```

No-keyword scheduled pushes are the default when `keywords` is empty. Use optional `--event-source`, `--event-type`, or `--is-high-value` to narrow the structured full-list query.

OpenClaw cron runs:

```bash
python push_runtime.py run-once --openclaw-output
```

Daily summary, when active:

```bash
python push_runtime.py run-daily-summary --openclaw-output
```

`--openclaw-output` is the only delivery protocol. It prints the final platform-event message for OpenClaw cron to return. If the platform API has no deliverable events, the command prints exactly:

```text
NO_REPLY
```

## Core Rules

- The only source for event output is the deepseekdata API path implemented by this skill.
- Do not use any source outside the deepseekdata platform query to replace, modify, infer, or fabricate platform event results.
- Decide the event API by business intent:
  - If the user asks for a keyword/theme semantic match, configured topic push, daily topic summary, or keyword-based recent fetch, use the semantic event API through `manual-push`, `run-once`, or `run-daily-summary`.
  - If the user asks for a time-window/source/type/high-value/full-list/backfill/history pull and does not provide a keyword semantic intent, use the ReportServer ordinary event list API through `manual-push --keywords ""`, `run-once` with configured empty keywords, or `list-events`.
  - If the user asks to monitor newly arriving events in real time, this skill should not use the historical list API as a realtime stream; use the realtime WebSocket channel only when that is explicitly required by the surrounding system.
- Never reveal API keys, tokens, webhook URLs, account IDs, local config file paths, raw config JSON, environment variable values, or the step-by-step credential/config inspection process. If configuration matters, say only whether it is configured, missing, active, inactive, or changed.
- When relaying command failures, redact secret-looking values and keep the explanation operational. Do not mention remembered key values or where a key was found.
- Unless the user explicitly asks for technical implementation details, never put internal-only terms from the User-Facing Language Boundary into WeChat-facing replies.
- Empty API results are valid. Manual queries should report the empty platform result briefly; scheduled/OpenClaw runs should return `NO_REPLY`.
- Do not supplement empty deepseekdata results with any outside data.
- Time windows must match the user request or the configured schedule. Do not widen or shrink the window to force results.
- If the API key is missing, network fails, or the API response is malformed, report the failure. Do not fabricate events.

## File Structure

```text
event-intelligence/
├── SKILL.md
├── README.md
├── LICENSE.txt
├── push_runtime.py
├── event_query.py
├── runtime_config.py
├── rule_relevance.py
├── run_filter_matrix.py
└── run_rule_filter_review.py
```

`state/` is runtime state and may be created automatically. Do not include `state/` or `__pycache__/` in the upload package unless explicitly debugging local state.

## Runtime Config

Runtime config has these public fields. Do not print raw config files or secret-bearing fields in user-facing replies:

```json
{
  "active": false,
  "keywords": [],
  "event_source": "",
  "event_type": "",
  "is_high_value": "",
  "schedule": "5m",
  "page_size": 10,
  "retention_days": 5,
  "daily_summary_time": "09:00",
  "task_name": "OpenClaw-Event-Intelligence-Push",
  "history_file": "state/push_history.json",
  "last_push_time": "",
  "min_verification_comprehensive_score": 0.0,
  "min_semantic_score": 0.35,
  "filter_observability": "off"
}
```

Supported schedules:

- `5m`: every 5 minutes
- `15m`: every 15 minutes
- `60m`: every 60 minutes
- `24h`: every 24 hours
- `daily-0915`: 09:15 Asia/Shanghai
- `daily-1245`: 12:45 Asia/Shanghai
- `daily-1445`: 14:45 Asia/Shanghai

Keywords are limited to 3.

## API Key

Never echo the API key, even partially. After saving or checking it, report only `configured` or `missing`.

Save the deepseekdata API key with stdin:

```bash
printf "%s" "<API_KEY>" | python push_runtime.py set-api-key --key -
```

Or from a local file:

```bash
python push_runtime.py set-api-key --key-file "<KEY_FILE>"
```

## Scheduling

Before installing a schedule:

```bash
python push_runtime.py status
```

Then configure and install:

```bash
python push_runtime.py configure --active --keywords "" --schedule 5m --page-size 10
python push_runtime.py install-schedule
```

`install-schedule` builds OpenClaw cron jobs with:

- `sessionTarget: "isolated"`
- `payload.kind: "agentTurn"`
- a command that runs this skill and returns only command output

If the local `openclaw` CLI is available, it calls `openclaw cron add --json` directly. If not, it returns the JSON jobs and ready-to-run commands for the OpenClaw environment.

Stop runtime pushes:

```bash
python push_runtime.py uninstall-schedule
```

This disables local runtime config. Delete or disable the corresponding OpenClaw cron task in the platform when needed.

## Manual Fetch

```bash
python push_runtime.py manual-push --minutes <MINUTES> --keywords "<KEYWORD1>,<KEYWORD2>" --no-delivery
```

Manual fetch writes query history to `state/push_history.json`, so later detail lookup can resolve references such as “第 2 条详细看看”.

If manual fetch returns `events=[]`, `pushed=0`, `reason=no_events`, or otherwise has no platform events to show:

1. Report the deepseekdata result briefly, including the requested keyword and time window.
2. Do not query or present any non-deepseekdata source.
3. Do not save any non-API data into `state/push_history.json`.

For `run-once --openclaw-output`, `run-daily-summary --openclaw-output`, installed schedules, and cron tasks, an empty platform result should remain silent with `NO_REPLY`.

## Structured Event List

Use this path when the user needs historical/full event rows by structured filters and did not ask for semantic keyword relevance:

```bash
python push_runtime.py list-events --start "2026-06-01 00:00:00" --end "2026-06-01 23:59:59" --event-source "deepseekdata api" --page-size 100 --no-delivery
```

Rules for this path:

- It calls `GET /admin-api/aireport2/event-analysis/list`, not `/semantic/event/list`.
- `pageSize` must not exceed 100. The runtime paginates from `pageNo=1` until `total` is reached or a short page is returned.
- Use `eventId` as the downstream dedupe key.
- Prefer fixed, already-ended time windows for backfill, because the API sorts by publish time descending and live writes can cause repeated or drifting rows.
- The no-keyword list result is normalized through the same runtime event shape and formatter as keyword results. Remove only keyword-related fields (`matched_keywords`, `semanticScore`, `ruleFilterPassed`, `ruleFilterReason`, `matchedIncludeTerms`, `matchedExcludeTerms`) from no-keyword events.
- Normal list output should only show/store the configured summary fields used by event pushes, not deep-report fields.
- The ordinary list API can support later detail/deep-report lookup because its rows include full event-analysis fields such as `investmentLogic`, `overallReasoningChain`, `keyRisks`, and `analysisMetadata`; fetch those fields only after the user asks for a specific event detail. The only missing behavior versus the semantic API is keyword relevance filtering and semantic score semantics.

## Details

For a referenced event:

```bash
python push_runtime.py detail-from-ref --query "<USER_QUERY>"
```

Resolve the event from recent `state/push_history.json` first, then call the deepseekdata detail API. If no history match exists, stop and report that the event is not in recent pushed/query history.

If the referenced event came from `list-events`, refetch through the ordinary list API using `eventId` with the known publish-time/source/type constraints, then generate detail/deep-report fields. Do not save or show deep-report fields during the initial list fetch.

## CLI Quick Reference

```bash
python push_runtime.py status
python push_runtime.py configure --active --keywords "" --schedule 5m --page-size 10
python push_runtime.py install-schedule
python push_runtime.py run-once --openclaw-output
python push_runtime.py run-daily-summary --openclaw-output
python push_runtime.py manual-push --minutes 60 --keywords "" --no-delivery
python push_runtime.py list-events --start "2026-06-01 00:00:00" --end "2026-06-01 23:59:59" --event-source "deepseekdata api" --no-delivery
python push_runtime.py detail-from-ref --query "第 1 条详细看看"
python push_runtime.py uninstall-schedule
```
