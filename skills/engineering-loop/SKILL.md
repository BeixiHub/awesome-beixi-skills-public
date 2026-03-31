---
name: engineering-loop
description: Repository execution loop for debugging, implementation, refactoring, and code review across one or many files. Use when working in local repositories, tracing bugs, editing code, reviewing diffs, validating behavior after changes, or turning a user request into inspected, implemented, and verified code changes.
---

# Engineering Loop

Use an inspect-plan-implement-verify loop for code work.

## Workflow

1. Inspect before touching files.
- Find entry points, config, tests, and the smallest relevant file set.
- Reproduce the bug or locate the behavior boundary before guessing.

2. Restate the objective and constraints.
- Note what must not regress.
- Decide what can be verified locally.

3. Patch in small slices.
- Prefer one concern per patch.
- Preserve existing patterns unless they are the bug.
- Keep user edits intact.

4. Verify proportionally.
- Run targeted tests first.
- Then lint, typecheck, or build if relevant and affordable.
- If you cannot verify something, say exactly what remains unproven.

5. Close with outcome.
- Summarize what changed, what was verified, and what still carries risk.

## Special Cases

- Code review: findings first, severity-ordered, file and line anchored.
- Large refactor: split into mechanical changes, behavior changes, then cleanup.
- Unknown bug: reproduce or search logs and traces before proposing a fix.

## Tool Notes

- Prefer `rg` and `rg --files` for discovery.
- Keep edits surgical and diff-aware.
- Write durable project lessons back to `memory/*.md` when they will matter again.
