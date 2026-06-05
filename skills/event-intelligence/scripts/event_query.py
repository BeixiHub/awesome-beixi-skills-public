"""
事件查询工具
- search_events(keyword, minutes, ...): 批量检索事件摘要
- get_event_detail(keyword, event_id): 获取单个事件详情
- list_events(...): 按结构化条件分页拉取普通事件列表

底层直连 deepseekdata / ReportServer 事件查询 API。
"""

from __future__ import annotations

import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from rule_relevance import apply_rule_filter
from runtime_config import resolve_event_api_key

DATE_FMT = "%Y-%m-%d %H:%M:%S"
MAX_KEYWORDS = 3
SEMANTIC_EVENT_LIST_URL = "https://admin.deepseekdata.com/admin-api/aireport2/event-analysis/semantic/event/list"
STRUCTURED_EVENT_LIST_URL = "https://admin.deepseekdata.com/admin-api/aireport2/event-analysis/list"
EVENT_LIST_URL = SEMANTIC_EVENT_LIST_URL
DEFAULT_TIMEOUT = 60
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 4096
DEFAULT_MIN_VERIFICATION_COMPREHENSIVE_SCORE = 0.0
DEFAULT_MIN_SEMANTIC_SCORE = 0.35
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_DIR = SCRIPT_DIR / "state"
FILTER_OBSERVABILITY_ENV = "EVENT_INTEL_FILTER_OBSERVABILITY"
RULE_FILTER_METRICS_PATH = STATE_DIR / "rule_filter_metrics.jsonl"
RULE_FILTER_REJECTIONS_PATH = STATE_DIR / "rule_filter_rejections.jsonl"
RULE_FILTER_CANDIDATES_PATH = STATE_DIR / "rule_filter_candidates.jsonl"


def reset_event_api_client() -> None:
    """Compatibility hook for runtime key updates; urllib has no client cache."""
    return None


def _request(params: dict, *, url: str = SEMANTIC_EVENT_LIST_URL) -> dict:
    api_key = resolve_event_api_key()
    if not api_key:
        raise RuntimeError(
            "缺少 deepseekdata API key。请先通过 OpenClaw、运行时配置或 "
            "EVENT_INTEL_API_KEY/DEEPSEEKDATA_API_KEY 环境变量提供。"
        )

    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"X-API-Key": api_key, "tenant-id": "1"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            raw_bytes = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        body = (
            exc.read(MAX_ERROR_BODY_BYTES).decode("utf-8", errors="replace")
            if exc.fp
            else ""
        )
        raise RuntimeError(f"deepseekdata HTTP 错误 {exc.code}：{body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"deepseekdata 请求失败：{exc.reason}") from exc

    if len(raw_bytes) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"deepseekdata 响应超过大小限制：{MAX_RESPONSE_BYTES} 字节")
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"deepseekdata 返回的不是有效 JSON（位置 {exc.pos}）。") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"deepseekdata 返回结果结构异常：{json.dumps(raw, ensure_ascii=False)[:300]}"
        )
    if raw.get("code") != 0:
        raise RuntimeError(f"deepseekdata API 错误：{raw.get('msg')}")
    return raw["data"]


def _semantic_request(params: dict) -> dict:
    return _request(params, url=SEMANTIC_EVENT_LIST_URL)


def _structured_request(params: dict) -> dict:
    return _request(params, url=STRUCTURED_EVENT_LIST_URL)


def _safe_get(d: dict, *keys, default=None):
    """安全地按路径取嵌套字典的值"""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def _score(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _passes_score_filters(
    item: dict,
    *,
    min_verification_comprehensive_score: float | None,
    min_semantic_score: float | None,
) -> bool:
    if min_verification_comprehensive_score is not None:
        score = _score(item.get("verificationComprehensiveScore"))
        if score is None or score < min_verification_comprehensive_score:
            return False
    if min_semantic_score is not None:
        score = _score(item.get("semanticScore"))
        if score is None or score < min_semantic_score:
            return False
    return True


_BJT = timezone(timedelta(hours=8))


# 去除摘要开头的"研报"/"研报指出"等前缀及紧跟的标点
_REPORT_PREFIX_RE = re.compile(
    r'^\s*(?:研报指出|研报认为|研报显示|研报提到|研报表示|研报)'
    r'[，,：:；;、。\s]*'
)


def _strip_report_prefix(text: str | None) -> str | None:
    """去除摘要文本开头的"研报指出"等冗余前缀。"""
    if not text:
        return text
    return _REPORT_PREFIX_RE.sub('', text, count=1)


def _format_ts(ts) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts / 1000, tz=_BJT).strftime(DATE_FMT)


def _safe_format_ts(ts) -> str | None:
    try:
        return _format_ts(ts)
    except (TypeError, ValueError, OSError):
        return str(ts) if ts is not None else None


def _event_publish_date(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return _safe_format_ts(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _event_title(item: dict) -> str | None:
    return item.get("compliantTitle") or item.get("eventTitle") or item.get("title")


def _project_event_summary(
    item: dict,
    *,
    rule_result: dict | None = None,
) -> dict:
    meta = item.get("analysisMetadata", {})
    if not isinstance(meta, dict):
        meta = {}
    core_logic = meta.get("core_logic_output", {})
    if not isinstance(core_logic, dict):
        core_logic = {}
    ic_report = meta.get("ic_report_v10_output", {})
    if not isinstance(ic_report, dict):
        ic_report = {}

    projected = {
        "eventId": item.get("eventId"),
        "compliantTitle": _event_title(item),
        "eventTitle": item.get("eventTitle"),
        "eventType": item.get("eventType"),
        "eventSource": item.get("eventSource"),
        "eventPublishDate": _event_publish_date(item.get("eventPublishDate")),
        "verificationComprehensiveScore": item.get("verificationComprehensiveScore"),
        "semanticScore": item.get("semanticScore"),
        "isHighValue": item.get("isHighValue"),
        "signalLevel": _safe_get(core_logic, "signal_hint", "level"),
        "original_summary": _strip_report_prefix(
            core_logic.get("original_summary") or item.get("oneSentenceSummary")
        ),
        "summary": _strip_report_prefix(ic_report.get("summary") or item.get("oneSentenceSummary")),
    }
    if rule_result is not None:
        projected.update(
            {
                "ruleFilterPassed": rule_result["ruleFilterPassed"],
                "ruleFilterReason": rule_result["ruleFilterReason"],
                "matchedIncludeTerms": rule_result["matchedIncludeTerms"],
                "matchedExcludeTerms": rule_result["matchedExcludeTerms"],
            }
        )
    return projected


def _project_event_detail(item: dict) -> dict:
    meta = item.get("analysisMetadata", {})
    if not isinstance(meta, dict):
        meta = {}
    core_logic = meta.get("core_logic_output", {})
    if not isinstance(core_logic, dict):
        core_logic = {}
    ic_report = meta.get("ic_report_v10_output", {})
    if not isinstance(ic_report, dict):
        ic_report = {}
    logic_validation = meta.get("logic_validation_output", {})
    if not isinstance(logic_validation, dict):
        logic_validation = {}
    logic_library = meta.get("logic_library_output", {})
    if not isinstance(logic_library, dict):
        logic_library = {}

    targets_summary = []
    for t in item.get("investmentTargetsSummary", []) or []:
        if not isinstance(t, dict):
            continue
        targets_summary.append(
            {
                "relevance": t.get("relevance"),
                "target_code": t.get("target_code"),
                "target_name": t.get("target_name"),
                "research_opinion": t.get("research_opinion"),
            }
        )

    return {
        "compliantTitle": _event_title(item),
        "eventTitle": item.get("eventTitle"),
        "eventType": item.get("eventType"),
        "eventSource": item.get("eventSource"),
        "eventPublishDate": _event_publish_date(item.get("eventPublishDate")),
        "oneSentenceSummary": item.get("oneSentenceSummary"),
        "signalLevel": _safe_get(core_logic, "signal_hint", "level"),
        "original_summary": _strip_report_prefix(
            core_logic.get("original_summary") or item.get("oneSentenceSummary")
        ),
        "summary": _strip_report_prefix(ic_report.get("summary") or item.get("oneSentenceSummary")),
        "investmentTargetsSummary": targets_summary,
        "investmentLogic": item.get("investmentLogic"),
        "overallReasoningChain": item.get("overallReasoningChain"),
        "keyRisks": item.get("keyRisks"),
        "signalCategory": item.get("signalCategory"),
        "formatted_tree": ic_report.get("formatted_tree"),
        "transmission_logic": ic_report.get("transmission_logic"),
        "logic_library_output": logic_library,
        "historical_cases_analysis": logic_validation.get("historical_cases_analysis"),
        "analysisMetadata": meta,
    }


def _append_jsonl(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        return


def _filter_observability_mode() -> str:
    raw = os.getenv(FILTER_OBSERVABILITY_ENV, "off").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return "metrics"
    if raw in {"metrics", "debug", "off"}:
        return raw
    return "off"


def _trace_key(item: dict) -> str:
    event_id = str(item.get("eventId", "") or "").strip()
    if event_id:
        return f"id:{event_id}"
    title = str(item.get("title") or item.get("compliantTitle") or item.get("eventTitle") or "")
    return f"fallback:{title}:{item.get('eventPublishDate')}"


def _candidate_trace_record(
    *,
    keyword: str,
    item: dict,
    passes_score_filter: bool,
    rule_result: dict | None = None,
) -> dict:
    return {
        "traceKey": _trace_key(item),
        "keyword": keyword,
        "eventId": item.get("eventId"),
        "title": item.get("title") or item.get("compliantTitle") or item.get("eventTitle"),
        "eventPublishDate": _safe_format_ts(item.get("eventPublishDate")),
        "eventSource": item.get("eventSource"),
        "signalCategory": item.get("signalCategory"),
        "verificationComprehensiveScore": item.get("verificationComprehensiveScore"),
        "semanticScore": item.get("semanticScore"),
        "passesScoreFilter": passes_score_filter,
        "ruleFilterPassed": None if rule_result is None else rule_result.get("ruleFilterPassed"),
        "ruleFilterReason": "" if rule_result is None else rule_result.get("ruleFilterReason", ""),
        "matchedIncludeTerms": [] if rule_result is None else rule_result.get("matchedIncludeTerms", []),
        "matchedExcludeTerms": [] if rule_result is None else rule_result.get("matchedExcludeTerms", []),
        "returned": False,
    }


def _rule_rejection_record(
    *,
    keyword: str,
    minutes: int,
    item: dict,
    rule_result: dict,
    min_verification_comprehensive_score: float | None,
    min_semantic_score: float | None,
) -> dict:
    return {
        "ts": datetime.now(tz=_BJT).isoformat(),
        "keyword": keyword,
        "window_minutes": minutes,
        "eventId": item.get("eventId"),
        "title": item.get("title") or item.get("compliantTitle") or item.get("eventTitle"),
        "eventPublishDate": _safe_format_ts(item.get("eventPublishDate")),
        "eventSource": item.get("eventSource"),
        "signalCategory": item.get("signalCategory"),
        "verificationComprehensiveScore": item.get("verificationComprehensiveScore"),
        "semanticScore": item.get("semanticScore"),
        "min_verification_comprehensive_score": min_verification_comprehensive_score,
        "min_semantic_score": min_semantic_score,
        "ruleFilterPassed": rule_result.get("ruleFilterPassed"),
        "ruleFilterReason": rule_result.get("ruleFilterReason"),
        "matchedIncludeTerms": rule_result.get("matchedIncludeTerms", []),
        "matchedExcludeTerms": rule_result.get("matchedExcludeTerms", []),
    }


def _record_filter_metrics(
    *,
    keyword: str,
    minutes: int,
    requested_page_size: int,
    fetch_size: int,
    api_total: int,
    fetched_count: int,
    score_filtered_count: int,
    score_removed_count: int,
    rule_filtered_count: int,
    rule_removed_count: int,
    returned_count: int,
    rule_reason_counts: Counter,
    min_verification_comprehensive_score: float | None,
    min_semantic_score: float | None,
) -> None:
    if _filter_observability_mode() not in {"metrics", "debug"}:
        return
    _append_jsonl(
        RULE_FILTER_METRICS_PATH,
        {
            "ts": datetime.now(tz=_BJT).isoformat(),
            "keyword": keyword,
            "window_minutes": minutes,
            "requested_page_size": requested_page_size,
            "fetch_size": fetch_size,
            "api_total": api_total,
            "fetched_count": fetched_count,
            "score_filtered_count": score_filtered_count,
            "score_removed_count": score_removed_count,
            "rule_filtered_count": rule_filtered_count,
            "rule_removed_count": rule_removed_count,
            "returned_count": returned_count,
            "ruleFilterReason_counts": dict(rule_reason_counts),
            "min_verification_comprehensive_score": min_verification_comprehensive_score,
            "min_semantic_score": min_semantic_score,
        },
    )


def _record_candidate_trace(
    *,
    keyword: str,
    minutes: int,
    requested_page_size: int,
    fetch_size: int,
    candidates: list[dict],
    min_verification_comprehensive_score: float | None,
    min_semantic_score: float | None,
) -> None:
    if _filter_observability_mode() != "debug":
        return
    _append_jsonl(
        RULE_FILTER_CANDIDATES_PATH,
        {
            "ts": datetime.now(tz=_BJT).isoformat(),
            "observabilityKind": "filter-candidate-trace",
            "debugOnly": True,
            "keyword": keyword,
            "window_minutes": minutes,
            "requested_page_size": requested_page_size,
            "fetch_size": fetch_size,
            "min_verification_comprehensive_score": min_verification_comprehensive_score,
            "min_semantic_score": min_semantic_score,
            "candidates": candidates,
        },
    )


def _signal_level(item: dict) -> str:
    meta = item.get("analysisMetadata", {})
    core_logic = meta.get("core_logic_output", {})
    return _safe_get(core_logic, "signal_hint", "level") or "未知"


# ──────────────────────────────────────────────
#  第一段：批量检索事件摘要
# ──────────────────────────────────────────────
_LEVEL_PRIORITY = {"S级": 0, "A级": 1, "B级": 2, "C级": 3}


def _count_levels(levels: list[str]) -> dict:
    total = len(levels)
    s_count = sum(1 for level in levels if level == "S级")
    a_count = sum(1 for level in levels if level == "A级")
    return {
        "total": total,
        "S级": s_count,
        "A级": a_count,
        "other": total - s_count - a_count,
    }


def _better_level(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    current_priority = _LEVEL_PRIORITY.get(current, 99)
    candidate_priority = _LEVEL_PRIORITY.get(candidate, 99)
    return candidate if candidate_priority < current_priority else current


def search_events(
    keyword: str,
    minutes: int = 60,
    page_size: int = 10,
    min_verification_comprehensive_score: float | None = DEFAULT_MIN_VERIFICATION_COMPREHENSIVE_SCORE,
    min_semantic_score: float | None = DEFAULT_MIN_SEMANTIC_SCORE,
) -> dict:
    """
    按时间范围检索事件，返回精简摘要列表。
    内部拉取全量数据后按 S级优先 + 时间倒序 排序，返回 top page_size 条。

    Args:
        keyword:   语义检索关键词
        minutes:   时间窗口（分钟），end=当前时间，start=当前时间-minutes
        page_size: 最终返回条数（默认10）

    Returns:
        {"total": int, "events": [摘要字典]}
    """
    now = datetime.now(tz=_BJT)
    start = now - timedelta(minutes=minutes)

    fetch_size = page_size
    if min_verification_comprehensive_score is not None or min_semantic_score is not None:
        fetch_size = min(30, max(page_size, page_size * 3))

    params = {
        "keyword": keyword,
        "eventPublishDateStart": start.strftime(DATE_FMT),
        "eventPublishDateEnd": now.strftime(DATE_FMT),
        "pageNo": 1,
        "pageSize": fetch_size,
    }

    data = _semantic_request(params)
    events = []
    items = data.get("list", [])
    score_filtered_count = 0
    score_removed_count = 0
    rule_filtered_count = 0
    rule_removed_count = 0
    rule_reason_counts: Counter = Counter()
    observability_mode = _filter_observability_mode()
    record_debug_trace = observability_mode == "debug"
    candidate_trace: list[dict] = []

    for item in items:
        if not _passes_score_filters(
            item,
            min_verification_comprehensive_score=min_verification_comprehensive_score,
            min_semantic_score=min_semantic_score,
        ):
            score_removed_count += 1
            if record_debug_trace:
                candidate_trace.append(
                    _candidate_trace_record(
                        keyword=keyword,
                        item=item,
                        passes_score_filter=False,
                    )
                )
            continue
        score_filtered_count += 1
        rule_result = apply_rule_filter(keyword, item)
        rule_reason_counts[rule_result["ruleFilterReason"]] += 1
        if not rule_result["ruleFilterPassed"]:
            rule_removed_count += 1
            if record_debug_trace:
                candidate_trace.append(
                    _candidate_trace_record(
                        keyword=keyword,
                        item=item,
                        passes_score_filter=True,
                        rule_result=rule_result,
                    )
                )
                _append_jsonl(
                    RULE_FILTER_REJECTIONS_PATH,
                    _rule_rejection_record(
                        keyword=keyword,
                        minutes=minutes,
                        item=item,
                        rule_result=rule_result,
                        min_verification_comprehensive_score=min_verification_comprehensive_score,
                        min_semantic_score=min_semantic_score,
                    ),
                )
            continue
        rule_filtered_count += 1
        if record_debug_trace:
            candidate_trace.append(
                _candidate_trace_record(
                    keyword=keyword,
                    item=item,
                    passes_score_filter=True,
                    rule_result=rule_result,
                )
            )
        events.append(_project_event_summary(item, rule_result=rule_result))

    total = data.get("total", 0)

    events.sort(key=lambda e: e.get("eventPublishDate") or "", reverse=True)
    events.sort(key=lambda e: _LEVEL_PRIORITY.get(e.get("signalLevel"), 99))
    sorted_events = events
    returned_events = sorted_events[:page_size]
    if candidate_trace:
        returned_keys = {_trace_key(item) for item in returned_events}
        for candidate in candidate_trace:
            candidate["returned"] = candidate.get("traceKey") in returned_keys

    _record_filter_metrics(
        keyword=keyword,
        minutes=minutes,
        requested_page_size=page_size,
        fetch_size=fetch_size,
        api_total=int(total or 0),
        fetched_count=len(items),
        score_filtered_count=score_filtered_count,
        score_removed_count=score_removed_count,
        rule_filtered_count=rule_filtered_count,
        rule_removed_count=rule_removed_count,
        returned_count=len(returned_events),
        rule_reason_counts=rule_reason_counts,
        min_verification_comprehensive_score=min_verification_comprehensive_score,
        min_semantic_score=min_semantic_score,
    )
    _record_candidate_trace(
        keyword=keyword,
        minutes=minutes,
        requested_page_size=page_size,
        fetch_size=fetch_size,
        candidates=candidate_trace,
        min_verification_comprehensive_score=min_verification_comprehensive_score,
        min_semantic_score=min_semantic_score,
    )

    return {"total": total, "events": returned_events}


def _normalize_bool_param(value: bool | str | int | None) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "是", "高价值"}:
        return "true"
    if text in {"0", "false", "no", "n", "off", "否", "非高价值"}:
        return "false"
    return str(value).strip()


def list_events(
    *,
    event_publish_date_start: str | datetime,
    event_publish_date_end: str | datetime,
    event_source: str | None = None,
    event_type: str | None = None,
    is_high_value: bool | str | int | None = None,
    page_size: int = 100,
    max_events: int | None = None,
) -> dict:
    """
    按结构化条件从普通事件列表接口分页拉取事件。

    适用于无 keyword 的全量/补数/历史列表场景；不要用于语义相关性检索。
    下游应使用 eventId 去重。
    """
    def fmt(value: str | datetime) -> str:
        if isinstance(value, datetime):
            return value.astimezone(_BJT).strftime(DATE_FMT)
        return str(value).strip()

    params: dict[str, Any] = {
        "eventPublishDateStart": fmt(event_publish_date_start),
        "eventPublishDateEnd": fmt(event_publish_date_end),
        "pageNo": 1,
        "pageSize": max(1, min(int(page_size or 100), 100)),
    }
    if event_source:
        params["eventSource"] = str(event_source).strip()
    if event_type:
        params["eventType"] = str(event_type).strip()
    high_value = _normalize_bool_param(is_high_value)
    if high_value is not None:
        params["isHighValue"] = high_value

    events_by_id: dict[str, dict] = {}
    fallback_index = 0
    total = 0
    fetched = 0
    page_no = 1
    while True:
        params["pageNo"] = page_no
        data = _structured_request(params)
        items = data.get("list", [])
        if not isinstance(items, list):
            items = []
        total = int(data.get("total", 0) or 0)
        fetched += len(items)

        for item in items:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("eventId", "") or "").strip()
            if not event_id:
                fallback_index += 1
                event_id = (
                    f"fallback:{item.get('eventPublishDate')}:"
                    f"{_event_title(item)}:{fallback_index}"
                )
            if event_id not in events_by_id:
                events_by_id[event_id] = _project_event_summary(item)
        if max_events is not None and len(events_by_id) >= max(0, int(max_events)):
            break
        if fetched >= total or len(items) < params["pageSize"]:
            break
        page_no += 1

    events = list(events_by_id.values())
    events.sort(key=lambda item: str(item.get("eventPublishDate", "") or ""), reverse=True)
    events.sort(key=lambda item: _LEVEL_PRIORITY.get(str(item.get("signalLevel", "") or ""), 99))
    if max_events is not None:
        events = events[: max(0, int(max_events))]

    return {
        "total": total,
        "deduped_total": len(events_by_id),
        "fetched": fetched,
        "pages": page_no,
        "query": {
            "eventPublishDateStart": params["eventPublishDateStart"],
            "eventPublishDateEnd": params["eventPublishDateEnd"],
            "eventSource": params.get("eventSource", ""),
            "eventType": params.get("eventType", ""),
            "isHighValue": params.get("isHighValue", ""),
        },
        "events": events,
    }


# ──────────────────────────────────────────────
#  第二段：获取单个事件详情
# ──────────────────────────────────────────────
def get_event_detail(keyword: str, event_id: str) -> dict | None:
    """
    根据 eventId 获取单条事件的详细分析数据。

    Args:
        keyword:  语义检索关键词（接口必填）
        event_id: 事件ID

    Returns:
        详情字典，未找到返回 None
    """
    params = {
        "keyword": keyword,
        "eventId": event_id,
        "pageNo": 1,
        "pageSize": 1,
    }

    data = _semantic_request(params)
    items = data.get("list", [])
    if not items:
        return None

    return _project_event_detail(items[0])


def get_event_detail_from_structured_list(
    *,
    event_id: str,
    event_publish_date_start: str | datetime | None = None,
    event_publish_date_end: str | datetime | None = None,
    event_source: str | None = None,
    event_type: str | None = None,
    is_high_value: bool | str | int | None = None,
) -> dict | None:
    """
    从普通事件列表接口按 eventId 查找详情。

    ReportServer 列表接口返回完整事件分析字段；如后端不支持 eventId 参数，
    可配合时间窗/来源/类型缩小范围后在本地按 eventId 去重匹配。
    """
    if not str(event_id or "").strip():
        return None
    end = event_publish_date_end or datetime.now(tz=_BJT)
    start = event_publish_date_start or (datetime.now(tz=_BJT) - timedelta(days=7))

    def fmt(value: str | datetime) -> str:
        if isinstance(value, datetime):
            return value.astimezone(_BJT).strftime(DATE_FMT)
        return str(value).strip()

    params: dict[str, Any] = {
        "eventId": str(event_id).strip(),
        "eventPublishDateStart": fmt(start),
        "eventPublishDateEnd": fmt(end),
        "pageNo": 1,
        "pageSize": 100,
    }
    if event_source:
        params["eventSource"] = str(event_source).strip()
    if event_type:
        params["eventType"] = str(event_type).strip()
    high_value = _normalize_bool_param(is_high_value)
    if high_value is not None:
        params["isHighValue"] = high_value

    fetched = 0
    total = 0
    while True:
        data = _structured_request(params)
        items = data.get("list", [])
        if not isinstance(items, list):
            items = []
        total = int(data.get("total", 0) or 0)
        fetched += len(items)
        for item in items:
            if isinstance(item, dict) and str(item.get("eventId", "") or "") == str(event_id):
                return _project_event_detail(item)
        if fetched >= total or len(items) < params["pageSize"]:
            break
        params["pageNo"] += 1
    return None


# ──────────────────────────────────────────────
#  第三段：每日事件统计（S/A 级计数）
# ──────────────────────────────────────────────
def _summary_windows(start: datetime, end: datetime, chunk_minutes: int) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    step = timedelta(minutes=chunk_minutes)
    while cursor < end:
        window_end = min(cursor + step, end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def _fetch_summary_window(
    keyword: str,
    window_start: datetime,
    window_end: datetime,
    batch_size: int,
) -> dict:
    params = {
        "keyword": keyword,
        "eventPublishDateStart": window_start.strftime(DATE_FMT),
        "eventPublishDateEnd": window_end.strftime(DATE_FMT),
        "pageNo": 1,
        "pageSize": batch_size,
    }

    data = _semantic_request(params)
    total = int(data.get("total", 0) or 0)
    levels_by_id: dict[str, str] = {}
    fallback_index = 0

    def add_items(items: list[dict]) -> None:
        nonlocal fallback_index
        for item in items:
            event_id = item.get("eventId")
            if event_id is None:
                fallback_index += 1
                event_id = (
                    f"fallback:{window_start.strftime(DATE_FMT)}:"
                    f"{item.get('eventPublishDate')}:{item.get('compliantTitle')}:{fallback_index}"
                )
            levels_by_id[str(event_id)] = _signal_level(item)

    add_items(data.get("list", []))

    fetched = len(data.get("list", []))
    page = 2
    while fetched < total:
        params["pageNo"] = page
        data = _semantic_request(params)
        items = data.get("list", [])
        add_items(items)
        fetched += len(items)
        page += 1
        if not items:
            break

    return {
        "total": total,
        "levels_by_id": levels_by_id,
        "start": window_start.strftime(DATE_FMT),
        "end": window_end.strftime(DATE_FMT),
        "fetched": fetched,
    }


def daily_event_summary(keyword: str, minutes: int = 1440) -> dict:
    """
    统计过去 N 分钟（默认 1440 = 24h）内的 S 级和 A 级事件数量。

    采用分片 + 并发 + 分页拉取以获得准确计数。默认 24 小时会拆成
    4 个 6 小时窗口并行查询，避免单次 24 小时大窗口在上游超时。

    Returns:
        {
            "window_minutes": 1440,
            "total": 整体命中数,
            "S级": S级数量,
            "A级": A级数量,
            "other": 其他等级数量,
            "start": "2026-04-09 12:00:00",
            "end":   "2026-04-10 12:00:00"
        }
    """
    now = datetime.now(tz=_BJT)
    start = now - timedelta(minutes=minutes)

    batch_size = 50
    chunk_minutes = 360
    windows = _summary_windows(start=start, end=now, chunk_minutes=chunk_minutes)
    max_workers = min(4, len(windows)) or 1

    levels_by_id: dict[str, str] = {}
    chunk_results: list[dict] = []
    if len(windows) == 1:
        chunk_results.append(_fetch_summary_window(keyword, windows[0][0], windows[0][1], batch_size))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_fetch_summary_window, keyword, window_start, window_end, batch_size)
                for window_start, window_end in windows
            ]
            for future in as_completed(futures):
                chunk_results.append(future.result())

    chunk_results.sort(key=lambda item: item["start"])
    for chunk in chunk_results:
        levels_by_id.update(chunk["levels_by_id"])

    counts = _count_levels(list(levels_by_id.values()))

    return {
        "window_minutes": minutes,
        "total": counts["total"],
        "S级": counts["S级"],
        "A级": counts["A级"],
        "other": counts["other"],
        "start": start.strftime(DATE_FMT),
        "end": now.strftime(DATE_FMT),
        "chunk_minutes": chunk_minutes,
        "chunks": [
            {
                "start": chunk["start"],
                "end": chunk["end"],
                "total": chunk["total"],
                "fetched": chunk["fetched"],
            }
            for chunk in chunk_results
        ],
    }


def _keyword_daily_levels(
    keyword: str,
    *,
    start: datetime,
    end: datetime,
    chunk_minutes: int,
    batch_size: int,
) -> dict:
    windows = _summary_windows(start=start, end=end, chunk_minutes=chunk_minutes)
    max_workers = min(4, len(windows)) or 1

    chunk_results: list[dict] = []
    if len(windows) == 1:
        chunk_results.append(_fetch_summary_window(keyword, windows[0][0], windows[0][1], batch_size))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_fetch_summary_window, keyword, window_start, window_end, batch_size)
                for window_start, window_end in windows
            ]
            for future in as_completed(futures):
                chunk_results.append(future.result())

    chunk_results.sort(key=lambda item: item["start"])
    levels_by_id: dict[str, str] = {}
    for chunk in chunk_results:
        for event_id, level in chunk["levels_by_id"].items():
            levels_by_id[event_id] = _better_level(levels_by_id.get(event_id), level)

    counts = _count_levels(list(levels_by_id.values()))
    return {
        "keyword": keyword,
        "levels_by_id": levels_by_id,
        "summary": {
            "keyword": keyword,
            "total": counts["total"],
            "S级": counts["S级"],
            "A级": counts["A级"],
            "other": counts["other"],
        },
        "chunks": [
            {
                "start": chunk["start"],
                "end": chunk["end"],
                "total": chunk["total"],
                "fetched": chunk["fetched"],
            }
            for chunk in chunk_results
        ],
    }


def daily_event_summary_many(keywords: list[str], minutes: int = 1440) -> dict:
    """
    多关键词每日统计。

    每个关键词独立查询，返回分关键词统计；再按 eventId 全局去重，返回去重后的总数和等级分布。
    """
    cleaned_keywords: list[str] = []
    seen_keywords: set[str] = set()
    for keyword in keywords:
        cleaned = str(keyword).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen_keywords:
            continue
        seen_keywords.add(key)
        cleaned_keywords.append(cleaned)
    if not cleaned_keywords:
        cleaned_keywords = ["AI"]
    if len(cleaned_keywords) > MAX_KEYWORDS:
        raise RuntimeError(f"暂不支持超过 {MAX_KEYWORDS} 个关键词；请最多保留 {MAX_KEYWORDS} 个主题。")

    now = datetime.now(tz=_BJT)
    start = now - timedelta(minutes=minutes)
    batch_size = 50
    chunk_minutes = 360
    max_workers = min(3, len(cleaned_keywords)) or 1

    keyword_results: list[dict] = []
    if len(cleaned_keywords) == 1:
        keyword_results.append(
            _keyword_daily_levels(
                cleaned_keywords[0],
                start=start,
                end=now,
                chunk_minutes=chunk_minutes,
                batch_size=batch_size,
            )
        )
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _keyword_daily_levels,
                    keyword,
                    start=start,
                    end=now,
                    chunk_minutes=chunk_minutes,
                    batch_size=batch_size,
                )
                for keyword in cleaned_keywords
            ]
            for future in as_completed(futures):
                keyword_results.append(future.result())

    keyword_results.sort(key=lambda item: cleaned_keywords.index(item["keyword"]))

    merged_levels_by_id: dict[str, str] = {}
    for result in keyword_results:
        for event_id, level in result["levels_by_id"].items():
            merged_levels_by_id[event_id] = _better_level(merged_levels_by_id.get(event_id), level)

    merged_counts = _count_levels(list(merged_levels_by_id.values()))
    per_keyword = [result["summary"] for result in keyword_results]
    sum_keyword_total = sum(int(item.get("total", 0) or 0) for item in per_keyword)
    return {
        "window_minutes": minutes,
        "keywords": cleaned_keywords,
        "per_keyword": per_keyword,
        "sum_keyword_total": sum_keyword_total,
        "deduped_total": merged_counts["total"],
        "duplicates_removed": max(0, sum_keyword_total - merged_counts["total"]),
        "total": merged_counts["total"],
        "S级": merged_counts["S级"],
        "A级": merged_counts["A级"],
        "other": merged_counts["other"],
        "start": start.strftime(DATE_FMT),
        "end": now.strftime(DATE_FMT),
        "chunk_minutes": chunk_minutes,
        "chunks": [
            {
                "keyword": result["keyword"],
                "chunks": result["chunks"],
            }
            for result in keyword_results
        ],
    }


# ──────────────────────────────────────────────
#  直接运行时的演示
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("【第一段】批量检索事件摘要（最近 1440 分钟，keyword=AI）")
    print("=" * 60)
    result = search_events(keyword="AI", minutes=1440, page_size=10)
    print(f"共命中 {result['total']} 条事件，当前返回 {len(result['events'])} 条：\n")
    for i, ev in enumerate(result["events"], 1):
        print(f"  [{i}] eventId: {ev['eventId']}")
        print(f"      标题: {ev['compliantTitle']}")
        print(f"      发布时间: {ev['eventPublishDate']}")
        print(f"      信号等级: {ev['signalLevel']}")
        print(f"      原始摘要: {(ev['original_summary'] or '')[:80]}...")
        print(f"      产业链总结: {(ev['summary'] or '')[:80]}...")
        print()

    print("\n" + "=" * 60)
    print("【第二段】获取单条事件详情（eventId=76679）")
    print("=" * 60)
    detail = get_event_detail(keyword="AI", event_id="76679")
    if detail:
        print(json.dumps(detail, ensure_ascii=False, indent=2))
    else:
        print("未找到该事件")
