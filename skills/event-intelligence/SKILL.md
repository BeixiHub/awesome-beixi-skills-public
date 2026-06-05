---
name: event-intelligence-tool
description: Use event intelligence tools to fetch, list, summarize, configure, and inspect deepseekdata platform events; keep investor-facing replies concise and non-technical.
---

# Event Intelligence Tool Skill

Use the event intelligence tools first.

## Tool Routing

- Recent semantic topic query: call `event_intelligence_search_recent` with `keywords`, `minutes`, and `pageSize`.
- Recent no-keyword query: call `event_intelligence_search_recent` with `keywords: []`. This means full-list platform events for the requested recent window; add source/type/high-value filters only when the user asks for them.
- If `event_intelligence_search_recent` returns a `fallback` object, the semantic matching path was unavailable and the tool used platform event-list keyword text matching. Briefly say the platform event library was queried with a fallback match; do not describe API errors or internals.
- Fixed time-window, backfill, or explicit full-list query: call `event_intelligence_list_events` with `start`, `end`, source/type/high-value filters, and optional `limit`.
- Detail after a prior result: call `event_intelligence_get_detail` with the original user reference, such as `第2条详细看看`.
- Direct detail by id: call `event_intelligence_get_detail` with `eventId`; include `keyword` only when the event came from a semantic keyword result.
- Daily statistics: call `event_intelligence_daily_summary`. Manual user requests should run immediately; scheduled runs should respect the saved active/inactive setting.
- Scheduled push run: call `event_intelligence_run_once`.
- Push settings: call `event_intelligence_configure_push`, then call `event_intelligence_status` if confirmation is needed.
- Rule-filter observability: call `event_intelligence_filter_metrics` only for technical maintenance or debugging.

## User-Facing Boundary

The user is usually a professional investor reading a WeChat-style reply. Do not expose implementation details unless the user explicitly asks for technical details.

Avoid user-facing terms such as API, key, token, webhook, accountId, OpenClaw, cron, CLI, runtime, config, JSON, NO_REPLY, eventId, file paths, environment variables, stack traces, and raw status fields.

Translate instead:

- platform query -> 平台事件库
- empty result -> 本次平台事件库未命中
- configured/missing -> 已设置 / 尚未设置
- schedule/runtime -> 定时推送
- detail/history/eventId -> 这条事件 / 上一条推送

Normal replies should look like investment intelligence: result, source, time window, and a brief note. Do not paste raw JSON, progress logs, command names, tool internals, state paths, or scheduling internals.

## Core Rules

- Use only the deepseekdata platform event results returned by these tools.
- Do not supplement empty platform results with outside data.
- Keep the requested time window exact; do not widen it to force results.
- Empty manual results are valid and should be reported briefly.
- Empty scheduled runs should remain silent in the platform workflow.
- Never reveal or partially echo credentials, webhook URLs, account IDs, local config paths, raw config, or environment variable values.
- If credentials, network, or malformed data block the query, report the operational failure in plain language.
