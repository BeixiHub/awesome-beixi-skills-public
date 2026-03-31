# PLAYBOOKS.md - Execution Loops

Use this file when a task is multi-step, research-heavy, code-heavy, or finance-heavy. Pair it with `routing-rules.json`.

## Quick Routing

- Prior context, preferences, or project recall -> run `memory_search` first.
- Latest info, comparison, or source-backed explanation -> use the research loop.
- Debugging, implementation, code review, or refactor -> use the engineering loop.
- Company, stock, fund, macro, or valuation question -> use the finance loop.
- Long writing task -> outline, draft, then polish.
- If the user asks about a stock/company/sector/metric or asks "怎么看/分析/比较/最新/现在/走势/值不值", immediately route into the finance loop and prefer QVeris before web search.

## Research Loop

1. Frame the question and the decision behind it.
2. Search broad, then narrow toward primary sources.
3. Read only the highest-value sources.
4. Compare facts, disagreements, and dates.
5. Synthesize with the conclusion first.

Guidelines:

- Prefer primary sources whenever possible.
- Use absolute dates for recent events.
- If the web provider does not support language filtering, do not rely on the `language` parameter; steer through query wording and source selection instead.
- Separate facts, inference, and open questions.

Deliverable:

- Core conclusion
- Evidence
- Caveats
- Next action

## Engineering Loop

1. Inspect before editing.
2. Find the smallest relevant file set.
3. Restate the target and constraints.
4. Patch in small slices.
5. Run targeted verification.
6. Summarize behavior change, verification, and open risk.

Guidelines:

- Preserve user edits.
- Avoid large speculative refactors.
- For code review, findings come first.
- Write durable lessons back to memory files when they will matter again.

## Finance Loop

1. Resolve entity, code, and market.
2. Lane A: web context for news, filings, guidance, and industry context.
3. Lane B: QVeris for quote, basics, statements, flows, and macro data.
4. Reconcile both lanes instead of dumping them separately.
5. Write for financially literate readers, not for tool demos.
6. If the message contains company/stock/fund/index/code/financial terms or "怎么看/分析/比较/最新/现在/走势/值不值", treat it as a finance task by default and start with QVeris if available.

Default sections:

- Core view
- Valuation or market positioning
- Fundamentals
- Financial quality
- Flows or technicals when relevant
- Catalysts
- Risks
- What to watch next

Guidelines:

- Lead with the conclusion.
- Do not dump raw tool output.
- If the latest structured data is missing, step back to the next best period and say so.
- Default to a polished, ready-to-forward layout with short blocks, visible separators, and minimal tool trace.

## Memory Capture

- Stable preferences, identity, or durable rules -> `MEMORY.md`
- Dated progress -> `memory/YYYY-MM-DD.md`
- Project-specific durable context -> named files under `memory/*.md`
- Shared-system persona sync -> `shared-persona/workbuddy-bridge.*`

## Output Contract

- High signal
- Concrete
- Minimal filler
- Minimal tool theater
