"""
事件语义检索工具
- search_events(keyword, minutes, ...): 批量检索事件摘要
- get_event_detail(keyword, event_id): 获取单个事件详情

底层通过 Qveris 平台调用 deepseekdata 语义事件检索工具，无需本地
持有 deepseekdata API Key，只需设置 QVERIS_TOKEN 环境变量。
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import re
from threading import Lock
from typing import Optional

from qveris_client import QVerisClient

DATE_FMT = "%Y-%m-%d %H:%M:%S"
MAX_KEYWORDS = 3

# Qveris 上 deepseekdata 事件语义检索工具的固定 ID。
# 若该 tool 被重命名或下线，首次 search 时会显式报错指出。
QVERIS_TOOL_ID = "deepseekdata.event_analysis.events.list.v1.32c03d20"

# 实测单响应 ~155KB，设 300KB 留 ~2x 余量防 schema 扩展。
_MAX_RESP = 300_000

_client_singleton: Optional[QVerisClient] = None
# 进程生命周期内复用 search_id；进程重启会重新获取。
_search_id_cache: Optional[str] = None
_client_lock = Lock()
_search_id_lock = Lock()


def _get_client() -> QVerisClient:
    global _client_singleton
    if _client_singleton is None:
        with _client_lock:
            if _client_singleton is None:
                _client_singleton = QVerisClient()
    return _client_singleton


def reset_qveris_client() -> None:
    global _client_singleton, _search_id_cache
    with _client_lock:
        _client_singleton = None
    with _search_id_lock:
        _search_id_cache = None


def _ensure_search_id(client: QVerisClient) -> str:
    global _search_id_cache
    if _search_id_cache:
        return _search_id_cache
    with _search_id_lock:
        if _search_id_cache:
            return _search_id_cache
        result = client.search_tools(
            query="deepseekdata semantic event list financial industry",
            limit=10,
        )
        # Qveris /search doesn't return a `success` flag — the presence of
        # `search_id` is the only reliable success indicator. Validate shape
        # before indexing so we emit a clear diagnostic instead of a KeyError.
        search_id = result.get("search_id") if isinstance(result, dict) else None
        if not isinstance(search_id, str) or not search_id:
            raise RuntimeError(
                f"Qveris 工具搜索返回结构异常：{json.dumps(result, ensure_ascii=False)[:300]}"
            )
        tool_ids = [t.get("tool_id") for t in result.get("results", [])]
        if QVERIS_TOOL_ID not in tool_ids:
            raise RuntimeError(
                f"Qveris 搜索结果中未找到预期工具 {QVERIS_TOOL_ID}。"
                f"实际返回工具：{tool_ids}。请确认工具是否被移除或重命名。"
            )
        _search_id_cache = search_id
        return _search_id_cache


def _request(params: dict) -> dict:
    client = _get_client()
    envelope = client.execute_tool(
        tool_id=QVERIS_TOOL_ID,
        search_id=_ensure_search_id(client),
        parameters=params,
        max_response_size=_MAX_RESP,
    )
    if not envelope.get("success"):
        raise RuntimeError(f"Qveris 工具执行失败：{envelope}")

    result = envelope.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError(
            f"Qveris 返回结果类型异常：{type(result).__name__}"
        )

    # Broker 层故障：upstream HTTP 非 200 或 result.error 不为空。
    # 要在尝试解包 data 之前拦截，否则错误会被后面的 shape check 吞掉，诊断变模糊。
    upstream_status = result.get("status_code")
    if upstream_status is not None and upstream_status != 200:
        raise RuntimeError(
            f"Qveris broker 上游 HTTP 错误 {upstream_status}："
            f"{result.get('message') or result.get('error')}"
        )
    if result.get("error"):
        raise RuntimeError(f"Qveris broker 错误：{result.get('error')}")

    # 正常路径：result.data 直接是 deepseekdata 原始响应 {code, msg, data: {list, total}}
    raw = result.get("data")
    # 截断路径：原始响应 > max_response_size 时 Qveris 改放 full_content_file_url，
    # 需要额外下载一次拿到完整响应（内容结构与正常路径一致）。
    if raw is None and result.get("full_content_file_url"):
        raw = client.download_full_content(result["full_content_file_url"])
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Qveris 返回结果结构异常：{json.dumps(result, ensure_ascii=False)[:300]}"
        )
    if raw.get("code") != 0:
        raise RuntimeError(f"deepseekdata API 错误：{raw.get('msg')}")
    return raw["data"]


def _safe_get(d: dict, *keys, default=None):
    """安全地按路径取嵌套字典的值"""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


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

    params = {
        "keyword": keyword,
        "eventPublishDateStart": start.strftime(DATE_FMT),
        "eventPublishDateEnd": now.strftime(DATE_FMT),
        "pageNo": 1,
        "pageSize": page_size,
    }

    data = _request(params)
    events = []
    for item in data.get("list", []):
        meta = item.get("analysisMetadata", {})
        core_logic = meta.get("core_logic_output", {})
        ic_report = meta.get("ic_report_v10_output", {})

        events.append({
            "eventId": item.get("eventId"),
            "compliantTitle": item.get("compliantTitle"),
            "eventPublishDate": _format_ts(item.get("eventPublishDate")),
            "signalLevel": _safe_get(core_logic, "signal_hint", "level"),
            "original_summary": _strip_report_prefix(core_logic.get("original_summary")),
            "summary": _strip_report_prefix(ic_report.get("summary")),
        })

    total = data.get("total", 0)

    events.sort(key=lambda e: e.get("eventPublishDate") or "", reverse=True)
    events.sort(key=lambda e: _LEVEL_PRIORITY.get(e.get("signalLevel"), 99))
    sorted_events = events

    return {"total": total, "events": sorted_events[:page_size]}


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

    data = _request(params)
    items = data.get("list", [])
    if not items:
        return None

    item = items[0]
    meta = item.get("analysisMetadata", {})
    core_logic = meta.get("core_logic_output", {})
    ic_report = meta.get("ic_report_v10_output", {})
    logic_validation = meta.get("logic_validation_output", {})
    logic_library = meta.get("logic_library_output", {})

    targets_summary = []
    for t in item.get("investmentTargetsSummary", []):
        targets_summary.append(
            {
                "relevance": t.get("relevance"),
                "target_code": t.get("target_code"),
                "target_name": t.get("target_name"),
                "research_opinion": t.get("research_opinion"),
            }
        )

    return {
        "compliantTitle": item.get("compliantTitle"),
        "eventPublishDate": _format_ts(item.get("eventPublishDate")),
        "signalLevel": _safe_get(core_logic, "signal_hint", "level"),
        "original_summary": _strip_report_prefix(core_logic.get("original_summary")),
        "summary": _strip_report_prefix(ic_report.get("summary")),
        "investmentTargetsSummary": targets_summary,
        "investmentLogic": item.get("investmentLogic"),
        "overallReasoningChain": item.get("overallReasoningChain"),
        "keyRisks": item.get("keyRisks"),
        "signalCategory": item.get("signalCategory"),
        "formatted_tree": ic_report.get("formatted_tree"),
        "transmission_logic": ic_report.get("transmission_logic"),
        "logic_library_output": logic_library,
        "historical_cases_analysis": logic_validation.get("historical_cases_analysis"),
    }


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

    data = _request(params)
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
        data = _request(params)
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
