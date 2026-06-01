"""Run deepseekdata score + rule-filter matrix for hotspot keywords.

This script calls the event-intelligence API path, saves raw candidate
projections, and records before/after counts for manual calibration.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

import event_query as _event_query
from event_query import (
    DATE_FMT,
    _passes_score_filters,
    _request,
)
from rule_relevance import RULES, apply_rule_filter


BJT = timezone(timedelta(hours=8))
DEFAULT_KEYWORDS = [
    "AI",
    "半导体",
    "新能源",
    "光伏",
    "锂电",
    "光通信",
    "PCB",
    "CPO",
    "创新药",
    "生物科技",
    "核聚变",
    "医疗器械",
    "机器人",
    "云计算",
    "储能",
    "电力",
    "军工",
    "汽车",
    "地产",
    "消费电子",
]
DEFAULT_WINDOWS = {
    "24h": 1,
    "7d": 7,
    "30d": 30,
}


def _score(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _project_event(keyword: str, item: dict, passes_score_filter: bool) -> dict:
    rule_result = apply_rule_filter(keyword, item) if passes_score_filter else {
        "ruleFilterPassed": False,
        "ruleFilterReason": "not evaluated: failed score filter",
        "matchedIncludeTerms": [],
        "matchedExcludeTerms": [],
    }
    return {
        "keyword": keyword,
        "eventId": item.get("eventId"),
        "title": item.get("compliantTitle") or item.get("title") or item.get("eventTitle"),
        "eventPublishDate": item.get("eventPublishDate"),
        "eventSource": item.get("eventSource"),
        "signalCategory": item.get("signalCategory"),
        "verificationComprehensiveScore": item.get("verificationComprehensiveScore"),
        "semanticScore": item.get("semanticScore"),
        "passesScoreFilter": passes_score_filter,
        "passes_proposed_filter": passes_score_filter,
        **rule_result,
    }


def _possible_false_kill(event: dict) -> bool:
    if event["ruleFilterPassed"] or not event["passesScoreFilter"]:
        return False
    has_include = bool(event.get("matchedIncludeTerms"))
    keyword = str(event.get("keyword") or "").lower()
    title = str(event.get("title") or "").lower()
    return has_include or bool(keyword and keyword in title)


def _possible_false_pass(event: dict) -> bool:
    if not event["ruleFilterPassed"]:
        return False
    keyword = event.get("keyword")
    if keyword not in RULES:
        return False
    if keyword == "医疗器械" and "手术机器人" in event.get("matchedIncludeTerms", []):
        return False
    return bool(event.get("matchedExcludeTerms")) or not bool(event.get("matchedIncludeTerms"))


def query_keyword(
    *,
    keyword: str,
    start: datetime,
    end: datetime,
    page_size: int,
    min_verification_comprehensive_score: float | None,
    min_semantic_score: float | None,
) -> dict:
    started = time.perf_counter()
    params = {
        "keyword": keyword,
        "eventPublishDateStart": start.strftime(DATE_FMT),
        "eventPublishDateEnd": end.strftime(DATE_FMT),
        "pageNo": 1,
        "pageSize": page_size,
    }
    data = _request(params)
    events = []
    for item in data.get("list", []):
        passes_score = _passes_score_filters(
            item,
            min_verification_comprehensive_score=min_verification_comprehensive_score,
            min_semantic_score=min_semantic_score,
        )
        events.append(_project_event(keyword, item, passes_score))

    before = sum(1 for event in events if event["passesScoreFilter"])
    after = sum(1 for event in events if event["passesScoreFilter"] and event["ruleFilterPassed"])
    return {
        "keyword": keyword,
        "request": params,
        "api_total": int(data.get("total", 0) or 0),
        "fetched": len(data.get("list", []) or []),
        "score_filtered_count": before,
        "rule_filtered_count": after,
        "rule_removed_count": before - after,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "items": events,
    }


def run_window(
    *,
    label: str,
    days: int,
    keywords: list[str],
    page_size: int,
    workers: int,
    min_verification_comprehensive_score: float | None,
    min_semantic_score: float | None,
) -> dict:
    end = datetime.now(tz=BJT)
    start = end - timedelta(days=days)
    results = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                query_keyword,
                keyword=keyword,
                start=start,
                end=end,
                page_size=page_size,
                min_verification_comprehensive_score=min_verification_comprehensive_score,
                min_semantic_score=min_semantic_score,
            ): keyword
            for keyword in keywords
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: keywords.index(item["keyword"]))
    all_events = [
        event
        for result in results
        for event in result["items"]
        if event["passesScoreFilter"]
    ]
    possible_false_kills = [event for event in all_events if _possible_false_kill(event)]
    possible_false_passes = [event for event in all_events if _possible_false_pass(event)]

    return {
        "window": {
            "label": label,
            "start": start.strftime(DATE_FMT),
            "end": end.strftime(DATE_FMT),
            "days": days,
        },
        "page_size": page_size,
        "filter": {
            "verificationComprehensiveScore_min": min_verification_comprehensive_score,
            "semanticScore_min": min_semantic_score,
            "rule_keywords": sorted(RULES.keys()),
        },
        "totals": [
            {
                "keyword": result["keyword"],
                "api_total": result["api_total"],
                "fetched": result["fetched"],
                "score_filtered_count": result["score_filtered_count"],
                "rule_filtered_count": result["rule_filtered_count"],
                "rule_removed_count": result["rule_removed_count"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
            for result in results
        ],
        "results": results,
        "possible_false_kills": possible_false_kills,
        "possible_false_passes": possible_false_passes,
    }


def _parse_optional_score(value: str) -> float | None:
    if value.lower() in {"none", "null", "off"}:
        return None
    return float(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", nargs="+", default=list(DEFAULT_WINDOWS))
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--min-verification", type=_parse_optional_score, default=0.0)
    parser.add_argument("--min-semantic", type=_parse_optional_score, default=0.35)
    parser.add_argument("--max-response-mb", type=int, default=25)
    parser.add_argument("--output-root", type=Path, default=Path("state/filter_matrix"))
    args = parser.parse_args()
    _event_query.MAX_RESPONSE_BYTES = max(5, args.max_response_mb) * 1024 * 1024

    requested_windows = []
    for label in args.windows:
        if label not in DEFAULT_WINDOWS:
            raise SystemExit(f"unknown window {label!r}; expected one of {', '.join(DEFAULT_WINDOWS)}")
        requested_windows.append((label, DEFAULT_WINDOWS[label]))

    base_dir = Path(__file__).resolve().parent
    stamp = datetime.now(tz=BJT).strftime("%Y%m%d_%H%M%S")
    outdir = args.output_root if args.output_root.is_absolute() else base_dir / args.output_root
    outdir = outdir / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    matrix_summary = {
        "generated_at": datetime.now(tz=BJT).isoformat(),
        "keywords": DEFAULT_KEYWORDS,
        "windows": [],
    }

    for label, days in requested_windows:
        print(f"running window={label} days={days} keywords={len(DEFAULT_KEYWORDS)}")
        window_result = run_window(
            label=label,
            days=days,
            keywords=DEFAULT_KEYWORDS,
            page_size=max(1, min(args.page_size, 100)),
            workers=max(1, args.workers),
            min_verification_comprehensive_score=args.min_verification,
            min_semantic_score=args.min_semantic,
        )
        window_path = outdir / f"{label}.json"
        window_path.write_text(json.dumps(window_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        score_total = sum(row["score_filtered_count"] for row in window_result["totals"])
        rule_total = sum(row["rule_filtered_count"] for row in window_result["totals"])
        removed_total = score_total - rule_total
        matrix_summary["windows"].append({
            "label": label,
            "path": str(window_path),
            "score_filtered_total": score_total,
            "rule_filtered_total": rule_total,
            "rule_removed_total": removed_total,
            "possible_false_kills": len(window_result["possible_false_kills"]),
            "possible_false_passes": len(window_result["possible_false_passes"]),
            "totals": window_result["totals"],
        })
        print(
            f"window={label} score_total={score_total} "
            f"rule_total={rule_total} removed={removed_total} "
            f"possible_false_kills={len(window_result['possible_false_kills'])} "
            f"possible_false_passes={len(window_result['possible_false_passes'])}"
        )

    summary_path = outdir / "summary.json"
    summary_path.write_text(json.dumps(matrix_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
