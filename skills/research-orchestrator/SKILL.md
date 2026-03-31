---
name: research-orchestrator
description: Structured research, fact-checking, latest-info lookup, and comparison workflow using search, source filtering, and synthesis. Use when the task depends on up-to-date information, source triangulation, citations, market/company research, trend scans, due diligence, or any answer that benefits from a search plan followed by evidence-backed conclusions.
---

# Research Orchestrator

Use a staged search-and-synthesis loop instead of ad-hoc browsing.

## Workflow

1. Frame the question in one sentence.
- Extract the target entities, date range, geography, and decision the user is trying to make.

2. Build a search ladder.
- Start broad to map the landscape.
- Narrow toward primary sources, official docs, filings, datasets, or direct evidence.
- Fetch only the highest-value pages.

3. Cross-check before concluding.
- Verify unstable facts with multiple sources when practical.
- Call out disagreements instead of smoothing them over.
- Use absolute dates for any recent event.

4. Synthesize like this.
- Conclusion first.
- Evidence next.
- Separate what is known, inferred, and still uncertain.
- End with the next best action if the user is making a decision.

## Tool Notes

- Use `web_search` to map the space, then `web_fetch` or direct page reads for the best sources.
- Avoid relying on `web_search.language` filters unless the provider explicitly supports them; prefer query wording plus source selection.
- For finance or macro topics, pair this skill with `QVeris` or `finance-dual-track`.
- When prior project decisions or user preferences matter, run `memory_search` before searching the web.

## Output Contract

- Prefer primary sources over tertiary summaries.
- State dates explicitly.
- If the source base is weak, say so plainly.
- Keep the final answer high-signal and citation-friendly rather than tool-centric.
