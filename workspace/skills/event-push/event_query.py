"""
事件语义检索工具
- search_events(keyword, minutes, ...): 批量检索事件摘要
- get_event_detail(keyword, event_id): 获取单个事件详情
"""

import requests
from datetime import datetime, timedelta, timezone

BASE_URL = "https://admin.deepseekdata.com/admin-api/aireport2/event-analysis/semantic/event/list"
HEADERS = {
    "X-API-Key": "sk-test-local-20260315",
    "tenant-id": "1",
}
DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _request(params: dict) -> dict:
    resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"API error: {result.get('msg')}")
    return result["data"]


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

def _format_ts(ts) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts / 1000, tz=_BJT).strftime(DATE_FMT)


# ──────────────────────────────────────────────
#  第一段：批量检索事件摘要
# ──────────────────────────────────────────────
_LEVEL_PRIORITY = {"S级": 0, "A级": 1, "B级": 2, "C级": 3}


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
            "original_summary": core_logic.get("original_summary"),
            "summary": ic_report.get("summary"),
        })

    total = data.get("total", 0)

    s_events = [e for e in events if e.get("signalLevel") == "S级"]
    other_events = [e for e in events if e.get("signalLevel") != "S级"]
    s_events.sort(key=lambda e: e.get("eventPublishDate") or "", reverse=True)
    other_events.sort(key=lambda e: e.get("eventPublishDate") or "", reverse=True)
    sorted_events = s_events + other_events

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
        targets_summary.append({
            "relevance": t.get("relevance"),
            "target_code": t.get("target_code"),
            "target_name": t.get("target_name"),
            "research_opinion": t.get("research_opinion"),
        })

    return {
        "compliantTitle": item.get("compliantTitle"),
        "eventPublishDate": _format_ts(item.get("eventPublishDate")),
        "signalLevel": _safe_get(core_logic, "signal_hint", "level"),
        "original_summary": core_logic.get("original_summary"),
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
