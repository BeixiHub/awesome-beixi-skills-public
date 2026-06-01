"""Offline rule-filter experiment for saved deepseekdata raw samples."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from rule_relevance import RULES, apply_rule_filter


DEFAULT_RAW = Path("state/filter_review_20260520_104708/raw.json")
DEFAULT_OUTPUT = Path("state/filter_review_20260520_104708/rule_review.json")


def _score(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_projection(keyword: str, item: dict) -> dict:
    rule_result = apply_rule_filter(keyword, item)
    return {
        "keyword": keyword,
        "eventId": item.get("eventId"),
        "title": item.get("title") or item.get("compliantTitle") or item.get("eventTitle"),
        "semanticScore": item.get("semanticScore"),
        "verificationComprehensiveScore": item.get("verificationComprehensiveScore"),
        "passes_proposed_filter": bool(item.get("passes_proposed_filter")),
        **rule_result,
    }


def _possible_false_kill(event: dict) -> bool:
    has_include = bool(event.get("matchedIncludeTerms"))
    has_keyword_literal = str(event.get("keyword") or "").lower() in str(event.get("title") or "").lower()
    return not event["ruleFilterPassed"] and (has_include or has_keyword_literal)


def _possible_false_pass(event: dict) -> bool:
    if not event["ruleFilterPassed"]:
        return False
    keyword = event.get("keyword")
    if keyword not in RULES:
        return False
    if keyword == "医疗器械" and "手术机器人" in event.get("matchedIncludeTerms", []):
        return False
    return bool(event.get("matchedExcludeTerms")) or not bool(event.get("matchedIncludeTerms"))


def build_review(raw_path: Path) -> dict:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    events = []
    keyword_counts = defaultdict(lambda: {"before": 0, "after": 0, "removed": 0})

    for block in raw.get("results", []):
        keyword = block.get("keyword")
        if not keyword:
            continue
        for item in block.get("items", []):
            if not item.get("passes_proposed_filter"):
                continue
            event = _event_projection(keyword, item)
            events.append(event)
            keyword_counts[keyword]["before"] += 1
            if event["ruleFilterPassed"]:
                keyword_counts[keyword]["after"] += 1
            else:
                keyword_counts[keyword]["removed"] += 1

    possible_false_kills = [event for event in events if _possible_false_kill(event)]
    possible_false_passes = [event for event in events if _possible_false_pass(event)]

    return {
        "source": str(raw_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter_policy": {
            "input_scope": "only events where passes_proposed_filter=true",
            "rule_keywords": sorted(RULES.keys()),
            "score_filter": raw.get("proposed_filter"),
        },
        "keyword_counts": [
            {"keyword": keyword, **counts}
            for keyword, counts in sorted(keyword_counts.items())
        ],
        "events": events,
        "possible_false_kills": possible_false_kills,
        "possible_false_passes": possible_false_passes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    raw_path = args.raw if args.raw.is_absolute() else base_dir / args.raw
    output_path = args.output if args.output.is_absolute() else base_dir / args.output

    review = build_review(raw_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    total_before = sum(row["before"] for row in review["keyword_counts"])
    total_after = sum(row["after"] for row in review["keyword_counts"])
    print(f"wrote {output_path}")
    print(f"total before={total_before} after={total_after} removed={total_before - total_after}")
    print("keyword before/after:")
    for row in review["keyword_counts"]:
        print(f"- {row['keyword']}: {row['before']} -> {row['after']} (removed {row['removed']})")
    print(f"possible_false_kills={len(review['possible_false_kills'])}")
    print(f"possible_false_passes={len(review['possible_false_passes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
