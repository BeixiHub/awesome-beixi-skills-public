from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import event_query as _event_query
from event_query import daily_event_summary_many, search_events
from runtime_config import (
    load_openclaw_feishu_config,
    resolve_event_api_key,
    split_env_values,
)

BJT = timezone(timedelta(hours=8))
ISO_FMT = "%Y-%m-%dT%H:%M:%S+08:00"
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_DIR = SCRIPT_DIR / "state"
PUSH_CONFIG_PATH = STATE_DIR / "push_config.json"
HISTORY_PATH = STATE_DIR / "push_history.json"
DEFAULT_TASK_NAME = "OpenClaw-Event-Intelligence-Push"
VALID_FEISHU_RECEIVE_ID_TYPES = {"chat_id", "open_id", "user_id", "union_id", "email"}
MAX_KEYWORDS = 3
SIGNAL_LEVEL_PRIORITY = {"S级": 0, "A级": 1, "B级": 2, "C级": 3}
DEFAULT_SCHEDULE = "5m"
LEGACY_INTERVAL_SCHEDULE_MAP = {
    5: "5m",
    15: "15m",
    60: "60m",
    1440: "24h",
}
SCHEDULE_PRESETS: dict[str, dict[str, Any]] = {
    "5m": {"label": "每5分钟", "lookback_minutes": 5, "cron": "*/5 * * * *"},
    "15m": {"label": "每15分钟", "lookback_minutes": 15, "cron": "*/15 * * * *"},
    "60m": {"label": "每60分钟", "lookback_minutes": 60, "cron": "0 * * * *"},
    "24h": {"label": "每24小时", "lookback_minutes": 1440, "cron": "0 0 * * *"},
    "daily-0915": {"label": "开盘前15分钟(09:15)", "lookback_minutes": 1440, "cron": "15 9 * * *"},
    "daily-1245": {"label": "下午开盘前15分钟(12:45)", "lookback_minutes": 1440, "cron": "45 12 * * *"},
    "daily-1445": {"label": "收盘前15分钟(14:45)", "lookback_minutes": 1440, "cron": "45 14 * * *"},
}
SCHEDULE_OPTION_ORDER = ("5m", "15m", "60m", "24h", "daily-0915", "daily-1245", "daily-1445")


def schedule_options_text() -> str:
    return "、".join(str(SCHEDULE_PRESETS[key]["label"]) for key in SCHEDULE_OPTION_ORDER)


def schedule_options_list() -> list[str]:
    return [str(SCHEDULE_PRESETS[key]["label"]) for key in SCHEDULE_OPTION_ORDER]


def schedule_label(schedule: str) -> str:
    return str(SCHEDULE_PRESETS.get(schedule, SCHEDULE_PRESETS[DEFAULT_SCHEDULE])["label"])


def schedule_hint_message(prefix: str, schedule: str) -> str:
    return f"{prefix}当前定时推送时间：{schedule_label(schedule)}。如需调整，可选：{schedule_options_text()}。"


def feishu_receive_id_type_options_text() -> str:
    return "、".join(sorted(VALID_FEISHU_RECEIVE_ID_TYPES))


def keyword_limit_message(count: int | None = None) -> str:
    prefix = f"当前提供了 {count} 个关键词。" if count is not None else ""
    return f"{prefix}暂不支持超过 {MAX_KEYWORDS} 个关键词；请最多保留 {MAX_KEYWORDS} 个主题。"


def split_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    else:
        text = str(value or "").strip()
        if not text:
            return []
        raw_items = [
            item.strip()
            for item in re.split(r"[,，、;；/\n\r\t]+|和|与", text)
        ]

    keywords: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        cleaned = item.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(cleaned)
    return keywords


def validate_keywords(value: Any) -> list[str]:
    keywords = split_keywords(value)
    if not keywords:
        keywords = ["AI"]
    if len(keywords) > MAX_KEYWORDS:
        raise RuntimeError(keyword_limit_message(len(keywords)))
    return keywords


def keywords_label(keywords: list[str]) -> str:
    return " / ".join(keywords)


CHINESE_NUMERAL_MAP = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def chinese_numeral_to_int(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in CHINESE_NUMERAL_MAP:
        return CHINESE_NUMERAL_MAP[text]
    if "十" not in text:
        return None
    left, _, right = text.partition("十")
    tens = CHINESE_NUMERAL_MAP.get(left, 1) if left else 1
    ones = CHINESE_NUMERAL_MAP.get(right, 0) if right else 0
    result = tens * 10 + ones
    return result if result > 0 else None


def parse_event_ref(query: str) -> dict[str, Any]:
    """Parse user phrases such as "第3条详细看看" or "我要看第三个深度报告"."""
    text = str(query or "").strip()
    compact = re.sub(r"\s+", "", text)
    batch_offset = 1 if re.search(r"上一轮|上一次|上轮|前一轮", compact) else 0

    index: int | None = None
    index_patterns = (
        r"第(?P<num>\d+|[零〇一二两三四五六七八九十]+)(?:条|个|则|篇)?",
        r"(?P<num>\d+|[零〇一二两三四五六七八九十]+)(?:条|个|则|篇)(?:.*?)(?:详细|详情|深度|看看|看一下)",
    )
    for pattern in index_patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        index = chinese_numeral_to_int(match.group("num"))
        if index is not None:
            break

    title_keyword = compact
    title_keyword = re.sub(r"上一轮|上一次|上轮|前一轮|刚才|刚刚|本轮|这轮|推送|事件", "", title_keyword)
    title_keyword = re.sub(r"第(?:\d+|[零〇一二两三四五六七八九十]+)(?:条|个|则|篇)?", "", title_keyword)
    title_keyword = re.sub(r"\d+(?:条|个|则|篇)", "", title_keyword)
    title_keyword = re.sub(
        r"详细看看|详细看一下|详细|详情|深度报告|深度分析|报告|看看|看一下|我要看|我想看|关于|那条|那个|这个",
        "",
        title_keyword,
    ).strip(" ，,。.!！?？：:；;、")
    if len(title_keyword) < 2:
        title_keyword = ""

    return {
        "raw": text,
        "batch_offset": batch_offset,
        "index": index,
        "title_keyword": title_keyword,
    }


def now_bjt() -> datetime:
    return datetime.now(tz=BJT)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(BJT).strftime(ISO_FMT)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BJT)
    return parsed.astimezone(BJT)


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def save_json(path: Path, payload: Any) -> None:
    ensure_state_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError(f"写入运行时状态失败：{path}。请确认目录存在且当前用户有写入权限。") from exc


def _resolve_runtime_file(value: Any, default: Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path


def runtime_history_path() -> Path:
    raw = load_json(PUSH_CONFIG_PATH, default={})
    value = raw.get("history_file") if isinstance(raw, dict) else ""
    return _resolve_runtime_file(value, HISTORY_PATH)


def save_history(history: dict[str, Any]) -> None:
    save_json(runtime_history_path(), history)


def persist_last_error(detail: str) -> None:
    try:
        raw_cfg = load_json(PUSH_CONFIG_PATH, default={})
        retention_days = 5
        if isinstance(raw_cfg, dict):
            try:
                retention_days = int(raw_cfg.get("retention_days", 5) or 5)
            except (TypeError, ValueError):
                retention_days = 5
        retention_days = max(1, min(retention_days, 30))
        history = load_history(retention_days=retention_days)
        history["last_run_time"] = to_iso(now_bjt())
        history["last_error"] = detail
        prune_history(history, retention_days=retention_days)
        save_history(history)
    except Exception:
        return


def normalize_schedule(value: Any) -> str:
    schedule = str(value or "").strip()
    if schedule in SCHEDULE_PRESETS:
        return schedule
    return DEFAULT_SCHEDULE


def normalize_legacy_interval_schedule(value: Any) -> str | None:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return LEGACY_INTERVAL_SCHEDULE_MAP.get(minutes)


def validate_schedule(value: Any) -> str:
    schedule = str(value or "").strip()
    if schedule in SCHEDULE_PRESETS:
        return schedule
    raise RuntimeError(f"暂不支持这个推送时间：{value!r}。当前只支持：{schedule_options_text()}。")


def schedule_lookback_minutes(schedule: str) -> int:
    preset = SCHEDULE_PRESETS[validate_schedule(schedule)]
    return int(preset["lookback_minutes"])


def schedule_sleep_seconds(schedule: str) -> int:
    schedule = validate_schedule(schedule)
    if not schedule.startswith("daily-"):
        return schedule_lookback_minutes(schedule) * 60

    raw_time = str(SCHEDULE_PRESETS[schedule]["cron"]).split()
    minute = int(raw_time[0])
    hour = int(raw_time[1])
    now = now_bjt()
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return max(1, int((next_run - now).total_seconds()))


def default_runtime_config() -> dict[str, Any]:
    return {
        "active": False,
        "keywords": ["AI"],
        "schedule": DEFAULT_SCHEDULE,
        "page_size": 10,
        "retention_days": 5,
        "daily_summary_time": "09:00",
        "task_name": DEFAULT_TASK_NAME,
        "feishu_webhooks": [],
        "feishu_receive_id": "",
        "feishu_receive_id_type": "chat_id",
        "event_intel_api_key": "",
        "history_file": "state/push_history.json",
        "last_push_time": "",
    }


def normalize_runtime_config(raw: Any) -> dict[str, Any]:
    cfg = default_runtime_config()
    if isinstance(raw, dict):
        for key in cfg:
            if key in raw:
                cfg[key] = raw[key]
        for key in ("api_key", "deepseekdata_api_key"):
            if key in raw:
                cfg[key] = raw[key]
        if not split_keywords(raw.get("keywords")) and "keyword" in raw:
            cfg["keywords"] = raw["keyword"]
        raw_schedule = str(raw.get("schedule", "") or "").strip()
        if raw_schedule not in SCHEDULE_PRESETS:
            legacy_schedule = normalize_legacy_interval_schedule(raw.get("interval_minutes"))
            if legacy_schedule:
                cfg["schedule"] = legacy_schedule

    cfg["active"] = bool(cfg.get("active", False))

    schedule = normalize_schedule(cfg.get("schedule"))
    cfg["schedule"] = schedule

    page_size = int(cfg.get("page_size", 10) or 10)
    cfg["page_size"] = max(1, min(page_size, 30))

    retention = int(cfg.get("retention_days", 5) or 5)
    cfg["retention_days"] = max(1, min(retention, 30))

    keywords = validate_keywords(cfg.get("keywords"))
    cfg["keywords"] = keywords

    task_name = str(cfg.get("task_name", DEFAULT_TASK_NAME) or DEFAULT_TASK_NAME).strip()
    cfg["task_name"] = task_name or DEFAULT_TASK_NAME

    api_key = str(cfg.get("event_intel_api_key", "") or "").strip()
    cfg["event_intel_api_key"] = api_key
    for key in ("api_key", "deepseekdata_api_key"):
        if key in cfg:
            cfg[key] = str(cfg.get(key, "") or "").strip()

    history_file = str(cfg.get("history_file", "state/push_history.json") or "").strip()
    cfg["history_file"] = history_file or "state/push_history.json"

    daily_time = str(cfg.get("daily_summary_time", "09:00") or "09:00").strip()
    if not _is_hhmm(daily_time):
        daily_time = "09:00"
    cfg["daily_summary_time"] = daily_time

    webhooks = cfg.get("feishu_webhooks", [])
    if isinstance(webhooks, str):
        webhooks = [webhooks]
    if not isinstance(webhooks, list):
        webhooks = []
    cfg["feishu_webhooks"] = [
        str(item).strip()
        for item in webhooks
        if isinstance(item, str) and str(item).strip()
    ]

    receive_id = str(cfg.get("feishu_receive_id", "") or "").strip()
    cfg["feishu_receive_id"] = receive_id

    receive_id_type = str(cfg.get("feishu_receive_id_type", "chat_id") or "chat_id").strip()
    if receive_id_type not in VALID_FEISHU_RECEIVE_ID_TYPES:
        receive_id_type = ""
    if not receive_id and not receive_id_type:
        receive_id_type = "chat_id"
    cfg["feishu_receive_id_type"] = receive_id_type
    return cfg


def load_runtime_config() -> dict[str, Any]:
    raw = load_json(PUSH_CONFIG_PATH, default={})
    return normalize_runtime_config(raw)


def apply_runtime_event_api_key(cfg: dict[str, Any], *, reset_client: bool = False) -> None:
    api_key = resolve_event_api_key(cfg)
    if not api_key:
        return
    previous = os.getenv("EVENT_INTEL_API_KEY", "")
    os.environ["EVENT_INTEL_API_KEY"] = api_key
    reset = getattr(_event_query, "reset_event_api_client", None)
    if callable(reset) and (reset_client or previous != api_key):
        reset()


def load_and_persist_runtime_config() -> dict[str, Any]:
    """Load config, apply schema/default normalization, then write it back."""
    cfg = load_runtime_config()
    save_json(PUSH_CONFIG_PATH, cfg)
    apply_runtime_event_api_key(cfg, reset_client=True)
    return cfg


def default_history(retention_days: int = 5) -> dict[str, Any]:
    return {
        "retention_days": retention_days,
        "max_batches": 1000,
        "batches": [],
        "sent_event_index": {},
        "last_run_time": "",
        "last_push_time": "",
        "last_error": "",
    }


def normalize_history(raw: Any, retention_days: int) -> dict[str, Any]:
    history = default_history(retention_days=retention_days)
    if isinstance(raw, dict):
        history.update(raw)

    if not isinstance(history.get("batches"), list):
        history["batches"] = []
    if not isinstance(history.get("sent_event_index"), dict):
        history["sent_event_index"] = {}

    cleaned_batches: list[dict[str, Any]] = []
    for batch in history["batches"]:
        if not isinstance(batch, dict):
            continue
        events = batch.get("events")
        if not isinstance(events, list) or not events:
            continue
        push_time = str(batch.get("push_time", "") or "")
        cleaned_batches.append(
            {
                "push_time": push_time,
                "keyword_label": str(batch.get("keyword_label", "") or ""),
                "keywords": split_keywords(batch.get("keywords")),
                "events": events,
                "source": str(batch.get("source", "runtime") or "runtime"),
            }
        )
    history["batches"] = cleaned_batches
    history["retention_days"] = retention_days
    history["max_batches"] = max(100, int(history.get("max_batches", 1000) or 1000))
    history["last_run_time"] = str(history.get("last_run_time", "") or "")
    history["last_push_time"] = str(history.get("last_push_time", "") or "")
    history["last_error"] = str(history.get("last_error", "") or "")
    return history


def load_history(retention_days: int) -> dict[str, Any]:
    raw = load_json(runtime_history_path(), default={})
    return normalize_history(raw, retention_days=retention_days)


def prune_history(history: dict[str, Any], retention_days: int) -> None:
    cutoff = now_bjt() - timedelta(days=retention_days)

    kept_batches: list[dict[str, Any]] = []
    for batch in history.get("batches", []):
        push_time = parse_iso(str(batch.get("push_time", "") or ""))
        if push_time is None:
            kept_batches.append(batch)
            continue
        if push_time >= cutoff:
            kept_batches.append(batch)

    history["batches"] = kept_batches[: int(history.get("max_batches", 1000) or 1000)]

    kept_sent: dict[str, str] = {}
    sent_idx = history.get("sent_event_index", {})
    if isinstance(sent_idx, dict):
        for key, value in sent_idx.items():
            stamp = parse_iso(str(value or ""))
            if stamp and stamp >= cutoff:
                kept_sent[str(key)] = to_iso(stamp)
    history["sent_event_index"] = kept_sent
    history["retention_days"] = retention_days


def compact_event(item: dict[str, Any]) -> dict[str, Any]:
    event_id = str(item.get("eventId", "") or "").strip()
    publish = str(item.get("eventPublishDate", "") or "").strip()
    title = str(item.get("compliantTitle", "") or "").strip()
    level = str(item.get("signalLevel", "") or "").strip()
    original_summary = str(item.get("original_summary", "") or "").strip()
    summary = str(item.get("summary", "") or "").strip()
    matched_keywords = split_keywords(item.get("matched_keywords", []))
    source_keyword = str(item.get("keyword", "") or "").strip()
    if source_keyword and source_keyword not in matched_keywords:
        matched_keywords.append(source_keyword)
    if not event_id and title:
        event_id = f"noid::{title}::{publish}"
    return {
        "eventId": event_id,
        "compliantTitle": title,
        "eventPublishDate": publish,
        "signalLevel": level,
        "original_summary": original_summary,
        "summary": summary,
        "matched_keywords": matched_keywords,
    }


def event_sort_key(event: dict[str, Any]) -> tuple[int, str]:
    level = str(event.get("signalLevel", "") or "")
    publish = str(event.get("eventPublishDate", "") or "")
    return (SIGNAL_LEVEL_PRIORITY.get(level, 99), publish)


def merge_keyword_events(keyword_results: list[dict[str, Any]], page_size: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    merged: dict[str, dict[str, Any]] = {}
    totals_by_keyword: dict[str, int] = {}

    for result in keyword_results:
        keyword = str(result.get("keyword", "") or "").strip()
        totals_by_keyword[keyword] = int(result.get("total", 0) or 0)
        for item in result.get("events", []):
            if not isinstance(item, dict):
                continue
            event = compact_event({**item, "keyword": keyword})
            event_id = str(event.get("eventId", "") or "").strip()
            if not event_id:
                continue
            existing = merged.get(event_id)
            if existing is None:
                merged[event_id] = event
                continue

            matched = existing.setdefault("matched_keywords", [])
            for matched_keyword in event.get("matched_keywords", []):
                if matched_keyword not in matched:
                    matched.append(matched_keyword)
            if event_sort_key(event) < event_sort_key(existing):
                preserved_keywords = list(matched)
                merged[event_id] = {**event, "matched_keywords": preserved_keywords}

    events = list(merged.values())
    events.sort(key=lambda item: str(item.get("eventPublishDate", "") or ""), reverse=True)
    events.sort(key=lambda item: SIGNAL_LEVEL_PRIORITY.get(str(item.get("signalLevel", "") or ""), 99))
    return events[:page_size], totals_by_keyword


def search_events_for_keywords(
    keywords: list[str],
    *,
    minutes: int,
    page_size: int,
) -> dict[str, Any]:
    keyword_results: list[dict[str, Any]] = []
    if len(keywords) == 1:
        result = search_events(keyword=keywords[0], minutes=minutes, page_size=page_size)
        keyword_results.append({"keyword": keywords[0], **result})
    else:
        with ThreadPoolExecutor(max_workers=len(keywords)) as executor:
            futures = {
                executor.submit(search_events, keyword=keyword, minutes=minutes, page_size=page_size): keyword
                for keyword in keywords
            }
            for future in as_completed(futures):
                keyword = futures[future]
                result = future.result()
                keyword_results.append({"keyword": keyword, **result})
        keyword_results.sort(key=lambda item: keywords.index(str(item.get("keyword", ""))))

    events, totals_by_keyword = merge_keyword_events(keyword_results, page_size=page_size)
    return {
        "keywords": keywords,
        "total": len(events),
        "sum_keyword_total": sum(totals_by_keyword.values()),
        "totals_by_keyword": totals_by_keyword,
        "events": events,
    }


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def build_push_text(
    *,
    keyword: str,
    minutes: int,
    events: list[dict[str, Any]],
    total: int,
    title: str = "事件推送",
    max_items: int | None = None,
    totals_by_keyword: dict[str, int] | None = None,
) -> str:
    stamp = now_bjt().strftime("%H:%M")
    limit = max_items or len(events)
    raw_total = sum(totals_by_keyword.values()) if totals_by_keyword else total
    lines = [
        (
            f"📡 {title} ({stamp})  —— 关键词「{keyword}」，最近 {minutes} 分钟"
            f"原始命中合计 {raw_total} 条，本次推送 {len(events)} 条（最多推送{limit}条）"
        ),
        "",
    ]
    if totals_by_keyword:
        totals_text = "；".join(f"{name} {count} 条" for name, count in totals_by_keyword.items())
        lines.append(f"分主题原始命中：{totals_text}")
        lines.append("")
    for idx, ev in enumerate(events, start=1):
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"[{idx}] {ev.get('compliantTitle') or '（无标题）'}")
        matched = ev.get("matched_keywords")
        if isinstance(matched, list) and matched:
            matched_text = " / ".join(str(item) for item in matched if str(item).strip())
            if matched_text:
                lines.append(f"    命中主题: {matched_text}")
        lines.append(
            f"    ⏰ {ev.get('eventPublishDate') or '-'} ｜ 信号等级: {ev.get('signalLevel') or '-'}"
        )
        lines.append(f"    📌 一句话总结: {clip(ev.get('original_summary') or '无', 200)}")
        lines.append(f"    📝 事件摘要: {clip(ev.get('summary') or '无', 500)}")
        lines.append("")
    lines.append("💡 对某条感兴趣？直接说「第X条详细看看」或描述标题关键词即可查看完整分析。")
    lines.append("🔄 想换个主题？说「关注半导体」即可切换关键词。")
    lines.append(f"⏱️ 想调整推送时间？可选：{schedule_options_text()}。")
    return "\n".join(lines)


def build_daily_summary_text(keyword_label: str, result: dict[str, Any]) -> str:
    stamp = now_bjt().strftime("%H:%M")
    start = result.get("start", "-")
    end = result.get("end", "-")
    s_count = result.get("S级", 0)
    a_count = result.get("A级", 0)
    other_count = result.get("other", 0)
    total = result.get("total", 0)
    lines = [
        f"📊 每日事件统计 ({stamp})",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔍 关键词: {keyword_label}",
        f"📅 统计周期: {start} ~ {end}（过去 24 小时）",
        "",
    ]

    per_keyword = result.get("per_keyword")
    if isinstance(per_keyword, list) and len(per_keyword) > 1:
        lines.append("分主题统计：")
        for item in per_keyword:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('keyword')}: S级 {item.get('S级', 0)} 条，"
                f"A级 {item.get('A级', 0)} 条，其他 {item.get('other', 0)} 条，"
                f"合计 {item.get('total', 0)} 条"
            )
        lines.append("")
        lines.append(
            f"去重后总数: {total} 条"
            f"（去重 {result.get('duplicates_removed', 0)} 条重复命中）"
        )

    lines.extend(
        [
            f"🔴 S 级事件: {s_count} 条",
            f"🟠 A 级事件: {a_count} 条",
            f"📋 其他等级: {other_count} 条",
            f"── 合计: {total} 条",
            "",
            "💡 想查看具体事件？说「查最近24小时的事件」。",
        ]
    )
    return "\n".join(lines)


def build_feishu_interactive_card(text: str) -> dict[str, Any]:
    """把推送纯文本转换为飞书 interactive 卡片内容。

    原版直接发送 msg_type=text，飞书群聊会按普通文本渲染，长摘要的层次感较弱。
    曾尝试改成 msg_type=post，但自建应用 API 对 post 的 content 嵌套更敏感，
    容易返回 230001 参数无效。这里统一使用 interactive 卡片：Webhook 发送
    {"msg_type": "interactive", "card": ...}，自建应用发送 msg_type=interactive
    且 content 为卡片 JSON 字符串，兼容性更稳定，也更接近 12:54 的详细阅读格式。
    """
    lines = text.splitlines()
    title = lines[0].strip() if lines and lines[0].strip() else "事件推送"
    content_lines = lines[1:] if lines else []
    elements: list[dict[str, Any]] = []
    for line in content_lines:
        if line == "━━━━━━━━━━━━━━━━━━━━━━━━":
            elements.append({"tag": "hr"})
            continue
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": line if line else " "},
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def post_to_feishu(webhook: str, text: str) -> dict[str, Any]:
    # 原版使用 msg_type=text：payload = {"msg_type": "text", "content": {"text": text}}
    # 修改为 msg_type=interactive，是为了让飞书 Webhook 按卡片段落渲染详细事件摘要，
    # 避开 post 在自建应用 API 下容易出现的参数嵌套兼容问题。
    payload = {"msg_type": "interactive", "card": build_feishu_interactive_card(text)}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"飞书 Webhook HTTP 错误 {exc.code}：{detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"飞书 Webhook 请求失败：{exc.reason}") from exc

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        parsed = {"raw": data}

    status_code = parsed.get("StatusCode")
    code = parsed.get("code")
    if status_code not in (None, 0) or code not in (None, 0):
        raise RuntimeError(f"飞书 Webhook 拒绝了本次消息：{parsed}")
    return parsed


def resolve_webhooks(cfg: dict[str, Any]) -> list[str]:
    webhooks = cfg.get("feishu_webhooks", [])
    if not isinstance(webhooks, list):
        webhooks = []
    env_webhooks = (
        split_env_values(os.getenv("FEISHU_WEBHOOKS"))
        + split_env_values(os.getenv("FEISHU_WEBHOOK_URL"))
        + split_env_values(os.getenv("FEISHU_WEBHOOK"))
    )
    openclaw_webhooks = load_openclaw_feishu_config().get("webhooks", [])
    if not isinstance(openclaw_webhooks, list):
        openclaw_webhooks = []
    candidates = [*webhooks, *env_webhooks, *openclaw_webhooks]
    cleaned: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            continue
        url = item.strip()
        if not url:
            continue
        if not url.startswith("https://"):
            continue
        if "replace-with-your-webhook" in url:
            continue
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


def resolve_feishu_app_config(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    openclaw_cfg = load_openclaw_feishu_config()
    runtime_cfg = cfg if isinstance(cfg, dict) else {}
    runtime_receive_id = str(runtime_cfg.get("feishu_receive_id", "") or "").strip()
    runtime_receive_id_type = (
        str(runtime_cfg.get("feishu_receive_id_type", "") or "").strip() if runtime_receive_id else ""
    )
    app_id = os.getenv("FEISHU_APP_ID", "").strip() or str(openclaw_cfg.get("app_id", "") or "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip() or str(
        openclaw_cfg.get("app_secret", "") or ""
    ).strip()
    receive_id = os.getenv("FEISHU_RECEIVE_ID", "").strip() or str(
        runtime_receive_id or openclaw_cfg.get("receive_id", "") or ""
    ).strip()
    receive_id_type = (
        os.getenv("FEISHU_RECEIVE_ID_TYPE", "").strip()
        or runtime_receive_id_type
        or str(openclaw_cfg.get("receive_id_type", "") or "").strip()
        or "chat_id"
    )
    if receive_id_type not in VALID_FEISHU_RECEIVE_ID_TYPES:
        receive_id_type = ""
    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "receive_id": receive_id,
        "receive_id_type": receive_id_type,
        "config_path": str(openclaw_cfg.get("config_path", "") or ""),
    }


def resolve_feishu_app_target(cfg: dict[str, Any] | None = None) -> dict[str, str] | None:
    target = resolve_feishu_app_config(cfg)
    if (
        not target.get("app_id")
        or not target.get("app_secret")
        or not target.get("receive_id")
        or target.get("receive_id_type") not in VALID_FEISHU_RECEIVE_ID_TYPES
    ):
        return None
    return target


def feishu_config_diagnostic(cfg: dict[str, Any]) -> dict[str, Any]:
    app_cfg = resolve_feishu_app_config(cfg)
    return {
        "webhook_count": len(resolve_webhooks(cfg)),
        "has_app_id": bool(app_cfg["app_id"]),
        "has_app_secret": bool(app_cfg["app_secret"]),
        "has_receive_id": bool(app_cfg["receive_id"]),
        "receive_id_type": app_cfg["receive_id_type"] if app_cfg["receive_id"] else "",
        "receive_id_configured_in_runtime": bool(str(cfg.get("feishu_receive_id", "") or "").strip()),
        "openclaw_config_path": app_cfg.get("config_path", ""),
    }


def has_event_api_key(cfg: dict[str, Any]) -> bool:
    return bool(resolve_event_api_key(cfg))


def event_api_key_missing_message() -> str:
    return (
        "缺少 deepseekdata API key。请先让用户提供 key，然后配置到 OpenClaw；"
        "如果是本地部署，也可以通过 stdin 或 --key-file 保存到运行时配置。"
    )


def set_event_api_key(api_key: str) -> dict[str, Any]:
    cleaned = api_key.strip()
    if not cleaned:
        raise RuntimeError("deepseekdata API key 不能为空。")
    cfg = load_runtime_config()
    cfg["event_intel_api_key"] = cleaned
    save_json(PUSH_CONFIG_PATH, cfg)
    apply_runtime_event_api_key(cfg, reset_client=True)
    return {
        "status": "ok",
        "message": "已保存 deepseekdata API key。",
        "path": str(PUSH_CONFIG_PATH),
        "event_api_has_key": True,
    }


def read_secret_file(path_value: str) -> str:
    path = Path(path_value).expanduser()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"读取密钥文件失败：{path}。请确认文件存在且当前用户有读取权限。") from exc


def configure_runtime(
    *,
    active: bool | None = None,
    keywords: str | list[str] | None = None,
    schedule: str | None = None,
    page_size: int | None = None,
    daily_summary_time: str | None = None,
) -> dict[str, Any]:
    cfg = load_runtime_config()
    if active is not None:
        cfg["active"] = active
    if keywords is not None:
        cfg["keywords"] = validate_keywords(keywords)
    if schedule is not None:
        cfg["schedule"] = validate_schedule(schedule)
    if page_size is not None:
        cfg["page_size"] = page_size
    if daily_summary_time is not None:
        cleaned_time = daily_summary_time.strip()
        if cleaned_time:
            cfg["daily_summary_time"] = cleaned_time

    cfg = normalize_runtime_config(cfg)
    save_json(PUSH_CONFIG_PATH, cfg)
    return {
        "status": "ok",
        "message": schedule_hint_message("已保存运行时推送配置。", cfg["schedule"]),
        "path": str(PUSH_CONFIG_PATH),
        "active": cfg["active"],
        "keywords": cfg["keywords"],
        "schedule": cfg["schedule"],
        "schedule_label": schedule_label(cfg["schedule"]),
        "available_schedules": schedule_options_list(),
        "lookback_minutes": schedule_lookback_minutes(cfg["schedule"]),
        "page_size": cfg["page_size"],
        "daily_summary_time": cfg["daily_summary_time"],
    }


def set_feishu_target(receive_id: str, receive_id_type: str = "chat_id") -> dict[str, Any]:
    cleaned_receive_id = receive_id.strip()
    cleaned_receive_id_type = (receive_id_type or "chat_id").strip()
    if not cleaned_receive_id:
        raise RuntimeError("飞书 receive_id 不能为空。")
    if cleaned_receive_id_type not in VALID_FEISHU_RECEIVE_ID_TYPES:
        raise RuntimeError(
            f"不支持的飞书 receive_id_type：{cleaned_receive_id_type!r}。"
            f"可选值：{feishu_receive_id_type_options_text()}。"
        )
    cfg = load_runtime_config()
    cfg["feishu_receive_id"] = cleaned_receive_id
    cfg["feishu_receive_id_type"] = cleaned_receive_id_type
    save_json(PUSH_CONFIG_PATH, cfg)
    return {
        "status": "ok",
        "message": "已保存飞书接收目标。",
        "path": str(PUSH_CONFIG_PATH),
        "receive_id_type": cleaned_receive_id_type,
        "has_receive_id": True,
        "has_feishu_target": has_feishu_target(cfg),
    }


def set_feishu_webhook(url: str, *, append: bool = False) -> dict[str, Any]:
    cleaned = url.strip()
    if not cleaned:
        raise RuntimeError("飞书 Webhook URL 不能为空。")
    if not cleaned.startswith("https://"):
        raise RuntimeError("飞书 Webhook URL 必须以 https:// 开头。")
    if "replace-with-your-webhook" in cleaned:
        raise RuntimeError("飞书 Webhook URL 仍是占位符，请替换为真实地址。")
    cfg = load_runtime_config()
    existing = cfg.get("feishu_webhooks", [])
    if not isinstance(existing, list):
        existing = []
    webhooks = [
        str(item).strip()
        for item in existing
        if isinstance(item, str) and str(item).strip()
    ]
    if append:
        if cleaned not in webhooks:
            webhooks.append(cleaned)
        cfg["feishu_webhooks"] = webhooks
    else:
        cfg["feishu_webhooks"] = [cleaned]
    save_json(PUSH_CONFIG_PATH, cfg)
    return {
        "status": "ok",
        "message": "已追加飞书 Webhook URL。" if append else "已保存飞书 Webhook URL。",
        "path": str(PUSH_CONFIG_PATH),
        "webhook_count": len(resolve_webhooks(cfg)),
        "has_feishu_target": has_feishu_target(cfg),
    }


def feishu_missing_target_message(cfg: dict[str, Any] | None = None) -> str:
    app_cfg = resolve_feishu_app_config(cfg)
    if app_cfg["app_id"] or app_cfg["app_secret"]:
        missing = []
        if not app_cfg["app_id"]:
            missing.append("App ID")
        if not app_cfg["app_secret"]:
            missing.append("App Secret")
        if not app_cfg["receive_id"]:
            missing.append("receive_id")
        if app_cfg["receive_id"] and not app_cfg["receive_id_type"]:
            missing.append("有效的 receive_id_type")
        return (
            "飞书自建应用配置不完整，缺少："
            f"{', '.join(missing)}。"
            "请提供缺失信息，或提供飞书 Webhook URL。"
            "如果要推送到群聊，请提供群聊 chat_id/receive_id。"
        )
    return (
        "尚未配置有效的飞书接收目标。请提供飞书 Webhook URL，"
        "或提供飞书自建应用的 App ID、App Secret 和 receive_id。"
        "如果要推送到群聊，请提供群聊 chat_id/receive_id。"
    )


def has_feishu_target(cfg: dict[str, Any]) -> bool:
    return bool(resolve_webhooks(cfg) or resolve_feishu_app_target(cfg))


def feishu_api_base() -> str:
    return os.getenv("FEISHU_API_BASE", "https://open.feishu.cn/open-apis").rstrip("/")


def post_json_request(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"飞书 API HTTP 错误 {exc.code}：{detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"飞书 API 请求失败：{exc.reason}") from exc
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"飞书 API 返回的不是 JSON：{data[:300]}") from exc


def fetch_feishu_tenant_access_token(app_id: str, app_secret: str) -> str:
    parsed = post_json_request(
        f"{feishu_api_base()}/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        {"Content-Type": "application/json; charset=utf-8"},
    )
    if parsed.get("code") != 0:
        raise RuntimeError(f"飞书 tenant_access_token 获取失败：{parsed}")
    token = str(parsed.get("tenant_access_token", "") or "")
    if not token:
        raise RuntimeError(f"飞书 token 响应缺少 tenant_access_token：{parsed}")
    return token


def post_to_feishu_app(target: dict[str, str], text: str) -> dict[str, Any]:
    receive_id_type = target.get("receive_id_type", "")
    if receive_id_type not in VALID_FEISHU_RECEIVE_ID_TYPES:
        raise RuntimeError(
            f"不支持的飞书 receive_id_type：{receive_id_type!r}。"
            f"可选值：{feishu_receive_id_type_options_text()}。"
        )
    token = fetch_feishu_tenant_access_token(target["app_id"], target["app_secret"])
    # 原版自建应用也发送 msg_type=text，content 是 {"text": text}。
    # post 富文本在自建应用 API 下可能触发 230001 参数无效；改用更稳定的卡片消息。
    content = json.dumps(build_feishu_interactive_card(text), ensure_ascii=False)
    parsed = post_json_request(
        f"{feishu_api_base()}/im/v1/messages?receive_id_type={receive_id_type}",
        {
            "receive_id": target["receive_id"],
            "msg_type": "interactive",
            "content": content,
        },
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    if parsed.get("code") != 0:
        raise RuntimeError(f"飞书自建应用拒绝了本次消息：{parsed}")
    return parsed


def post_text_to_feishu(cfg: dict[str, Any], text: str, dry_run: bool = False) -> list[dict[str, Any]]:
    if dry_run:
        return [{"method": "dry-run", "message": "试运行未实际发送飞书消息。", "result": {"ok": True}}]

    webhooks = resolve_webhooks(cfg)
    if webhooks:
        results: list[dict[str, Any]] = []
        for url in webhooks:
            try:
                result = post_to_feishu(url, text)
            except Exception as exc:  # noqa: BLE001
                results.append({"method": "webhook", "webhook": url, "ok": False, "error": str(exc)})
            else:
                results.append({"method": "webhook", "message": "飞书 Webhook 推送成功。", "webhook": url, "ok": True, "result": result})
        failed_results = [item for item in results if not item.get("ok")]
        if failed_results:
            errors = "; ".join(str(item.get("error", "")) for item in results if item.get("error"))
            raise RuntimeError(
                f"飞书 Webhook 推送部分失败：{len(failed_results)}/{len(results)} 个目标失败：{errors}"
            )
        return results

    app_target = resolve_feishu_app_target(cfg)
    if app_target:
        receive_id_type = app_target.get("receive_id_type", "chat_id")
        receive_id = app_target.get("receive_id", "")
        return [
            {
                "method": "app",
                "message": "飞书自建应用推送成功。",
                "receive_id_type": receive_id_type,
                "receive_id": "<redacted>" if receive_id else "",
                "result": post_to_feishu_app(app_target, text),
            }
        ]

    raise RuntimeError(feishu_missing_target_message(cfg))


def _event_is_new(event_id: str, sent_index: dict[str, str], cutoff: datetime) -> bool:
    value = sent_index.get(event_id)
    if not value:
        return True
    parsed = parse_iso(value)
    if parsed is None:
        return True
    return parsed < cutoff


def record_history_batch(
    history: dict[str, Any],
    *,
    keywords: list[str],
    events: list[dict[str, Any]],
    source: str,
    retention_days: int,
    update_sent_index: bool = True,
    update_last_push_time: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    batch_time = to_iso(now_bjt())
    sent_index = history.get("sent_event_index", {})
    if not isinstance(sent_index, dict):
        sent_index = {}
        history["sent_event_index"] = sent_index

    indexed_events: list[dict[str, Any]] = []
    for idx, event in enumerate(events, start=1):
        item = dict(event)
        item["index"] = idx
        indexed_events.append(item)
        event_id = str(item.get("eventId", "") or "")
        if update_sent_index and event_id:
            sent_index[event_id] = batch_time

    batch = {
        "push_time": batch_time,
        "keyword_label": keywords_label(keywords),
        "keywords": keywords,
        "source": source,
        "events": indexed_events,
    }
    history["batches"].insert(0, batch)
    if update_last_push_time:
        history["last_push_time"] = batch_time
    history["last_error"] = ""
    prune_history(history, retention_days=retention_days)
    return indexed_events, batch_time


def compact_batch_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": event.get("index"),
        "eventId": str(event.get("eventId", "") or ""),
        "compliantTitle": event.get("compliantTitle", ""),
        "eventPublishDate": event.get("eventPublishDate", ""),
        "signalLevel": event.get("signalLevel", ""),
        "matched_keywords": event.get("matched_keywords", []),
    }


def event_ref_keyword(batch: dict[str, Any], event: dict[str, Any]) -> str:
    matched = event.get("matched_keywords")
    if isinstance(matched, list):
        for item in matched:
            keyword = str(item or "").strip()
            if keyword:
                return keyword
    keywords = batch.get("keywords")
    if isinstance(keywords, list):
        for item in keywords:
            keyword = str(item or "").strip()
            if keyword:
                return keyword
    keyword_label = str(batch.get("keyword_label", "") or "").strip()
    return split_keywords(keyword_label)[0] if split_keywords(keyword_label) else "AI"


def resolve_event_ref_from_history(query: str, history: dict[str, Any]) -> dict[str, Any]:
    ref = parse_event_ref(query)
    batches = history.get("batches", [])
    if not isinstance(batches, list) or not batches:
        return {
            "status": "not_found",
            "reason": "no_push_history",
            "message": "未在最近的推送记录中找到该事件；当前没有可用推送批次。",
            "ref": ref,
            "latest_events": [],
        }

    batch_offset = int(ref.get("batch_offset", 0) or 0)
    if batch_offset >= len(batches):
        return {
            "status": "not_found",
            "reason": "batch_not_found",
            "message": "未在最近的推送记录中找到该事件；指定的历史批次不存在。",
            "ref": ref,
            "latest_events": [compact_batch_event(item) for item in batches[0].get("events", [])],
        }

    batch = batches[batch_offset]
    events = batch.get("events", [])
    if not isinstance(events, list) or not events:
        return {
            "status": "not_found",
            "reason": "empty_batch",
            "message": "未在最近的推送记录中找到该事件；该批次没有事件。",
            "ref": ref,
            "latest_events": [compact_batch_event(item) for item in batches[0].get("events", [])],
        }

    index = ref.get("index")
    if isinstance(index, int) and index > 0:
        for pos, event in enumerate(events, start=1):
            event_index = event.get("index", pos)
            try:
                event_index_int = int(event_index)
            except (TypeError, ValueError):
                event_index_int = pos
            if event_index_int == index:
                return {
                    "status": "matched",
                    "match_type": "index",
                    "ref": ref,
                    "batch": {
                        "push_time": batch.get("push_time", ""),
                        "keyword_label": batch.get("keyword_label", ""),
                        "source": batch.get("source", ""),
                        "batch_offset": batch_offset,
                    },
                    "keyword": event_ref_keyword(batch, event),
                    "event": event,
                }
        return {
            "status": "not_found",
            "reason": "index_not_found",
            "message": f"未在最近的推送记录中找到第 {index} 条事件。",
            "ref": ref,
            "latest_events": [compact_batch_event(item) for item in events],
        }

    title_keyword = str(ref.get("title_keyword", "") or "").casefold()
    if title_keyword:
        candidates: list[dict[str, Any]] = []
        for event in events:
            haystack = "\n".join(
                str(event.get(key, "") or "")
                for key in ("compliantTitle", "summary", "original_summary")
            ).casefold()
            if title_keyword in haystack:
                candidates.append(event)
        if len(candidates) == 1:
            event = candidates[0]
            return {
                "status": "matched",
                "match_type": "title_keyword",
                "ref": ref,
                "batch": {
                    "push_time": batch.get("push_time", ""),
                    "keyword_label": batch.get("keyword_label", ""),
                    "source": batch.get("source", ""),
                    "batch_offset": batch_offset,
                },
                "keyword": event_ref_keyword(batch, event),
                "event": event,
            }
        if len(candidates) > 1:
            return {
                "status": "ambiguous",
                "reason": "multiple_title_matches",
                "message": "在最近推送中匹配到多条事件，请指定序号。",
                "ref": ref,
                "candidates": [compact_batch_event(item) for item in candidates],
            }

    return {
        "status": "not_found",
        "reason": "no_match",
        "message": "该问题不在最近推送事件范围内；如需继续，我会显式说明并改用其他数据源生成报告。",
        "ref": ref,
        "latest_events": [compact_batch_event(item) for item in events],
    }


def detail_from_history_ref(query: str) -> dict[str, Any]:
    cfg = load_runtime_config()
    history = load_history(retention_days=cfg["retention_days"])
    prune_history(history, retention_days=cfg["retention_days"])
    resolved = resolve_event_ref_from_history(query, history)
    if resolved.get("status") != "matched":
        return resolved

    if not has_event_api_key(cfg):
        raise RuntimeError(event_api_key_missing_message())
    apply_runtime_event_api_key(cfg, reset_client=True)

    event = resolved.get("event", {})
    event_id = str(event.get("eventId", "") or "")
    if not event_id:
        return {
            "status": "not_found",
            "reason": "missing_event_id",
            "message": "最近推送记录中找到了该事件，但缺少 eventId，无法调用 deepseekdata 详情接口。",
            "resolved": resolved,
        }
    keyword = str(resolved.get("keyword", "") or "AI")
    detail_func = getattr(_event_query, "get_event_detail")
    detail = detail_func(keyword=keyword, event_id=event_id)
    if not detail:
        return {
            "status": "not_found",
            "reason": "detail_not_found",
            "message": "已在最近推送中找到该事件，但 deepseekdata 详情接口未返回详情。",
            "resolved": {
                **resolved,
                "event": compact_batch_event(event),
            },
        }
    return {
        "status": "ok",
        "source": "deepseekdata_api",
        "message": "已从最近推送事件中匹配，并通过 deepseekdata API 获取详情。",
        "keyword": keyword,
        "eventId": event_id,
        "resolved": {
            **resolved,
            "event": compact_batch_event(event),
        },
        "detail": detail,
    }


def run_once(force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    cfg = load_and_persist_runtime_config()

    retention_days = cfg["retention_days"]
    history = load_history(retention_days=retention_days)
    prune_history(history, retention_days=retention_days)

    if not cfg["active"] and not force:
        history["last_run_time"] = to_iso(now_bjt())
        history["last_error"] = ""
        save_history(history)
        return {
            "status": "skipped",
            "reason": "runtime_inactive",
            "message": "运行时推送未开启，本次未执行推送。",
            "active": False,
            "run_time": history["last_run_time"],
        }

    if not has_event_api_key(cfg):
        raise RuntimeError(event_api_key_missing_message())

    if not has_feishu_target(cfg) and not dry_run:
        raise RuntimeError(feishu_missing_target_message(cfg))

    lookback_minutes = schedule_lookback_minutes(cfg["schedule"])
    page_size = cfg["page_size"]
    keywords = validate_keywords(cfg.get("keywords"))
    keyword_label = keywords_label(keywords)

    result = search_events_for_keywords(
        keywords=keywords,
        minutes=lookback_minutes,
        page_size=page_size,
    )
    raw_events = result.get("events", [])
    compacted = [compact_event(item) for item in raw_events if isinstance(item, dict)]

    cutoff = now_bjt() - timedelta(days=retention_days)
    sent_index = history.get("sent_event_index", {})
    if not isinstance(sent_index, dict):
        sent_index = {}
        history["sent_event_index"] = sent_index

    new_events: list[dict[str, Any]] = []
    for event in compacted:
        event_id = event.get("eventId", "")
        if not event_id:
            continue
        if _event_is_new(event_id, sent_index, cutoff=cutoff):
            new_events.append(event)

    run_time = to_iso(now_bjt())
    history["last_run_time"] = run_time
    history["last_error"] = ""

    if not new_events:
        save_history(history)
        return {
            "status": "ok",
            "total": int(result.get("total", 0) or 0),
            "sum_keyword_total": int(result.get("sum_keyword_total", 0) or 0),
            "totals_by_keyword": result.get("totals_by_keyword", {}),
            "keywords": keywords,
            "pushed": 0,
            "reason": "no_new_events",
            "message": "本次没有发现新的事件，不需要推送。",
            "run_time": run_time,
            "lookback_minutes": lookback_minutes,
        }

    text = build_push_text(
        keyword=keyword_label,
        minutes=lookback_minutes,
        events=new_events,
        total=int(result.get("total", 0) or 0),
        max_items=page_size,
        totals_by_keyword=result.get("totals_by_keyword", {}),
    )

    feishu_results = post_text_to_feishu(cfg, text, dry_run=dry_run)
    real_sent = not dry_run

    indexed_events, batch_time = record_history_batch(
        history,
        keywords=keywords,
        events=new_events,
        source="runtime" if real_sent else "runtime_dry_run",
        retention_days=retention_days,
        update_sent_index=real_sent,
        update_last_push_time=real_sent,
    )
    save_history(history)
    if real_sent:
        cfg["last_push_time"] = batch_time
        save_json(PUSH_CONFIG_PATH, cfg)

    return {
        "status": "ok",
        "message": f"本次已推送 {len(indexed_events)} 条新事件。" if real_sent else f"试运行完成，发现 {len(indexed_events)} 条新事件，未标记为已推送。",
        "total": int(result.get("total", 0) or 0),
        "sum_keyword_total": int(result.get("sum_keyword_total", 0) or 0),
        "totals_by_keyword": result.get("totals_by_keyword", {}),
        "keywords": keywords,
        "pushed": len(indexed_events),
        "lookback_minutes": lookback_minutes,
        "run_time": run_time,
        "last_push_time": batch_time if real_sent else "",
        "feishu": feishu_results,
        "events": indexed_events,
    }


def manual_push(
    *,
    keywords: str | list[str] | None = None,
    minutes: int = 60,
    page_size: int | None = None,
    dry_run: bool = False,
    no_feishu: bool = False,
) -> dict[str, Any]:
    cfg = load_runtime_config()
    apply_runtime_event_api_key(cfg, reset_client=True)
    retention_days = cfg["retention_days"]
    history = load_history(retention_days=retention_days)
    prune_history(history, retention_days=retention_days)

    if keywords is not None:
        resolved_keywords = validate_keywords(keywords)
    else:
        resolved_keywords = validate_keywords(cfg.get("keywords"))
    resolved_keyword = keywords_label(resolved_keywords)
    resolved_minutes = max(1, int(minutes or 60))
    resolved_page_size = page_size if page_size and page_size > 0 else cfg["page_size"]
    resolved_page_size = max(1, min(int(resolved_page_size), 30))

    if not has_event_api_key(cfg):
        raise RuntimeError(event_api_key_missing_message())

    if not no_feishu and not dry_run and not has_feishu_target(cfg):
        raise RuntimeError(feishu_missing_target_message(cfg))

    result = search_events_for_keywords(
        keywords=resolved_keywords,
        minutes=resolved_minutes,
        page_size=resolved_page_size,
    )
    raw_events = result.get("events", [])
    compacted = [compact_event(item) for item in raw_events if isinstance(item, dict)]

    run_time = to_iso(now_bjt())
    history["last_run_time"] = run_time
    history["last_error"] = ""

    if not compacted:
        save_history(history)
        return {
            "status": "ok",
            "source": "manual",
            "total": int(result.get("total", 0) or 0),
            "sum_keyword_total": int(result.get("sum_keyword_total", 0) or 0),
            "totals_by_keyword": result.get("totals_by_keyword", {}),
            "keywords": resolved_keywords,
            "pushed": 0,
            "reason": "no_events",
            "message": "没有查询到可推送的事件。",
            "run_time": run_time,
            "events": [],
        }

    text = build_push_text(
        keyword=resolved_keyword,
        minutes=resolved_minutes,
        events=compacted,
        total=int(result.get("total", 0) or 0),
        title="手动事件推送",
        max_items=resolved_page_size,
        totals_by_keyword=result.get("totals_by_keyword", {}),
    )

    feishu_results: list[dict[str, Any]] = []
    if no_feishu:
        feishu_results.append({"method": "disabled", "message": "已按参数跳过飞书发送。", "result": {"ok": True}})
    else:
        feishu_results = post_text_to_feishu(cfg, text, dry_run=dry_run)
    real_sent = not dry_run and not no_feishu

    indexed_events, batch_time = record_history_batch(
        history,
        keywords=resolved_keywords,
        events=compacted,
        source="manual" if real_sent else ("manual_no_feishu" if no_feishu else "manual_dry_run"),
        retention_days=retention_days,
        update_sent_index=real_sent,
        update_last_push_time=real_sent,
    )
    save_history(history)
    if real_sent:
        cfg["last_push_time"] = batch_time
        save_json(PUSH_CONFIG_PATH, cfg)

    return {
        "status": "ok",
        "message": f"手动推送完成，已处理 {len(indexed_events)} 条事件。" if real_sent else f"手动查询完成，已处理 {len(indexed_events)} 条事件，未标记为已推送。",
        "source": "manual",
        "total": int(result.get("total", 0) or 0),
        "sum_keyword_total": int(result.get("sum_keyword_total", 0) or 0),
        "totals_by_keyword": result.get("totals_by_keyword", {}),
        "keywords": resolved_keywords,
        "pushed": len(indexed_events),
        "run_time": run_time,
        "last_push_time": batch_time if real_sent else "",
        "feishu": feishu_results,
        "text": text,
        "events": indexed_events,
    }


def run_daily(force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    cfg = load_and_persist_runtime_config()

    if not cfg["active"] and not force:
        return {
            "status": "skipped",
            "reason": "daily_summary_inactive",
            "message": "运行时推送未开启，本次未执行每日统计。",
        }

    if not has_event_api_key(cfg):
        raise RuntimeError(event_api_key_missing_message())

    if not has_feishu_target(cfg) and not dry_run:
        raise RuntimeError(feishu_missing_target_message(cfg))

    keywords = validate_keywords(cfg.get("keywords"))
    keyword = keywords_label(keywords)
    summary = daily_event_summary_many(keywords=keywords, minutes=1440)
    if int(summary.get("total", 0) or 0) <= 0:
        return {
            "status": "ok",
            "reason": "no_daily_events",
            "message": "过去 24 小时没有查询到可统计的事件。",
            "summary": summary,
        }

    text = build_daily_summary_text(keyword_label=keyword, result=summary)
    feishu_results = post_text_to_feishu(cfg, text, dry_run=dry_run)
    return {"status": "ok", "message": "每日事件统计已生成并发送。", "summary": summary, "feishu": feishu_results}


def _is_hhmm(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


def install_schedule(task_name: str | None = None) -> dict[str, Any]:
    cfg = load_and_persist_runtime_config()

    resolved_name = (task_name or cfg.get("task_name") or DEFAULT_TASK_NAME).strip()
    if not resolved_name:
        resolved_name = DEFAULT_TASK_NAME

    return install_unix_cron(resolved_name, cfg["schedule"], cfg["active"], cfg["daily_summary_time"])


def _read_crontab() -> list[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode == 0:
        return [line.rstrip("\n") for line in proc.stdout.splitlines()]
    err = (proc.stderr or "").lower()
    if "no crontab" in err:
        return []
    detail = (proc.stderr or proc.stdout or "未知错误").strip()
    raise RuntimeError(f"读取 crontab 失败：{detail}")


def _write_crontab(lines: list[str]) -> None:
    payload = "\n".join(lines).rstrip() + "\n"
    proc = subprocess.run(["crontab", "-"], input=payload, text=True, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "未知错误").strip()
        raise RuntimeError(f"写入 crontab 失败：{detail}")


def _drop_marked_cron(lines: list[str], marker_prefix: str) -> list[str]:
    out: list[str] = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            if _looks_like_cron_line(line):
                continue
        stripped = line.strip()
        if stripped.startswith(marker_prefix):
            skip_next = True
            continue
        out.append(line)
    return out


def _looks_like_cron_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if stripped.startswith("@"):
        return len(stripped.split(maxsplit=1)) == 2
    return len(stripped.split(maxsplit=5)) == 6


def _cron_run_once_line(schedule: str, python_bin: str, script: str) -> str:
    preset = SCHEDULE_PRESETS[validate_schedule(schedule)]
    run_cmd = f"{python_bin} {script} run-once --quiet"
    return f"{preset['cron']} {run_cmd} >/dev/null 2>&1"


def install_unix_cron(
    task_name: str,
    schedule: str,
    include_daily: bool,
    daily_time: str,
) -> dict[str, Any]:
    python_bin = shlex.quote(sys.executable)
    script = shlex.quote(str(Path(__file__).resolve()))
    marker_prefix = f"# EVENT_INTELLIGENCE:{task_name}:"
    lines = _read_crontab()
    lines = _drop_marked_cron(lines, marker_prefix)

    lines.append(f"{marker_prefix}main")
    lines.append(_cron_run_once_line(schedule, python_bin, script))

    installed = ["main"]
    if include_daily:
        hh, mm = daily_time.split(":")
        lines.append(f"{marker_prefix}daily")
        lines.append(f"{int(mm)} {int(hh)} * * * {python_bin} {script} run-daily-summary --quiet >/dev/null 2>&1")
        installed.append("daily")

    _write_crontab(lines)
    return {
        "status": "ok",
        "message": schedule_hint_message("已安装系统定时任务。", schedule),
        "scheduler": "crontab",
        "task_name": task_name,
        "installed_jobs": installed,
        "schedule": schedule,
        "schedule_label": schedule_label(schedule),
        "available_schedules": schedule_options_list(),
        "lookback_minutes": schedule_lookback_minutes(schedule),
    }


def uninstall_schedule(task_name: str | None = None) -> dict[str, Any]:
    cfg = load_runtime_config()
    resolved_name = (task_name or cfg.get("task_name") or DEFAULT_TASK_NAME).strip()
    if not resolved_name:
        resolved_name = DEFAULT_TASK_NAME

    marker_prefix = f"# EVENT_INTELLIGENCE:{resolved_name}:"
    lines = _read_crontab()
    cleaned = _drop_marked_cron(lines, marker_prefix)
    _write_crontab(cleaned)
    cfg["active"] = False
    save_json(PUSH_CONFIG_PATH, cfg)
    return {
        "status": "ok",
        "message": "已卸载系统定时任务，并关闭运行时推送。",
        "scheduler": "crontab",
        "task_name": resolved_name,
    }


def redacted_runtime_config(cfg: dict[str, Any]) -> dict[str, Any]:
    safe_cfg = dict(cfg)
    for key in ("event_intel_api_key", "api_key", "deepseekdata_api_key"):
        if str(safe_cfg.get(key, "") or "").strip():
            safe_cfg[key] = "<redacted>"
    webhooks = safe_cfg.get("feishu_webhooks")
    if isinstance(webhooks, list):
        safe_cfg["feishu_webhooks"] = ["<redacted>" for item in webhooks if str(item or "").strip()]
    elif str(webhooks or "").strip():
        safe_cfg["feishu_webhooks"] = ["<redacted>"]
    if str(safe_cfg.get("feishu_receive_id", "") or "").strip():
        safe_cfg["feishu_receive_id"] = "<redacted>"
    return safe_cfg


def status() -> dict[str, Any]:
    cfg = load_runtime_config()
    safe_cfg = redacted_runtime_config(cfg)
    history = load_history(retention_days=cfg["retention_days"])
    prune_history(history, retention_days=cfg["retention_days"])
    latest_batch = history["batches"][0] if history["batches"] else {}
    return {
        "status": "ok",
        "message": schedule_hint_message("运行时状态如下。", cfg["schedule"]),
        "push_config": safe_cfg,
        "feishu": feishu_config_diagnostic(cfg),
        "event_api": {
            "has_key": has_event_api_key(cfg),
            "stored_in_config": bool(
                str(cfg.get("event_intel_api_key", "") or "").strip()
                or str(cfg.get("api_key", "") or "").strip()
                or str(cfg.get("deepseekdata_api_key", "") or "").strip()
            ),
            "endpoint": "https://admin.deepseekdata.com",
        },
        "history": {
            "batches": len(history.get("batches", [])),
            "last_run_time": history.get("last_run_time", ""),
            "last_push_time": history.get("last_push_time", ""),
            "last_error": history.get("last_error", ""),
            "latest_batch_time": latest_batch.get("push_time", ""),
            "latest_batch_events": len(latest_batch.get("events", []))
            if isinstance(latest_batch.get("events"), list)
            else 0,
        },
    }


def run_loop(force: bool = False, dry_run: bool = False, max_iterations: int = 0) -> int:
    cfg = load_runtime_config()
    schedule = cfg["schedule"]
    iteration = 0
    stop_event = Event()
    previous_handlers: dict[int, Any] = {}

    def _request_stop(signum: int, _: Any) -> None:
        stop_event.set()

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            previous_handlers[int(sig)] = signal.getsignal(sig)
            signal.signal(sig, _request_stop)
        except (OSError, ValueError):
            pass

    try:
        while not stop_event.is_set():
            iteration += 1
            try:
                result = run_once(force=force, dry_run=dry_run)
                print(json.dumps({"loop_iteration": iteration, "result": result}, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                history = load_history(retention_days=cfg["retention_days"])
                history["last_run_time"] = to_iso(now_bjt())
                history["last_error"] = str(exc)
                save_history(history)
                print(
                    json.dumps(
                        {"loop_iteration": iteration, "status": "error", "detail": str(exc)},
                        ensure_ascii=False,
                    )
                )
            if max_iterations > 0 and iteration >= max_iterations:
                return 0
            stop_event.wait(schedule_sleep_seconds(schedule))
        return 0
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass


def cmd_init_config(args: argparse.Namespace) -> int:
    cfg = load_runtime_config()
    if PUSH_CONFIG_PATH.exists() and not args.force:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": "config_exists",
                    "message": "运行时配置已存在，未覆盖。需要覆盖时请加 --force。",
                    "path": str(PUSH_CONFIG_PATH),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    save_json(PUSH_CONFIG_PATH, cfg)
    print(
        json.dumps(
            {
                "status": "ok",
                "message": "已创建运行时配置。",
                "path": str(PUSH_CONFIG_PATH),
                "config": redacted_runtime_config(cfg),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_run_once(args: argparse.Namespace) -> int:
    result = run_once(force=args.force, dry_run=args.dry_run)
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_manual_push(args: argparse.Namespace) -> int:
    result = manual_push(
        keywords=args.keywords or None,
        minutes=args.minutes,
        page_size=args.page_size or None,
        dry_run=args.dry_run,
        no_feishu=args.no_feishu,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_run_daily(args: argparse.Namespace) -> int:
    result = run_daily(force=args.force, dry_run=args.dry_run)
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_set_event_api_key(args: argparse.Namespace) -> int:
    if args.key_file:
        api_key = read_secret_file(args.key_file)
    else:
        api_key = sys.stdin.read().strip() if args.key == "-" else args.key
    result = set_event_api_key(api_key)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    active: bool | None = None
    if args.active:
        active = True
    elif args.inactive:
        active = False
    result = configure_runtime(
        active=active,
        keywords=args.keywords,
        schedule=args.schedule,
        page_size=args.page_size,
        daily_summary_time=args.daily_summary_time,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_set_feishu_target(args: argparse.Namespace) -> int:
    result = set_feishu_target(args.receive_id, args.receive_id_type)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_set_feishu_webhook(args: argparse.Namespace) -> int:
    if args.url_file:
        url = read_secret_file(args.url_file)
    else:
        url = sys.stdin.read().strip() if args.url == "-" else args.url
    result = set_feishu_webhook(url, append=args.append)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    result = install_schedule(task_name=args.task_name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    result = uninstall_schedule(task_name=args.task_name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    return 0


def cmd_detail_from_ref(args: argparse.Namespace) -> int:
    result = detail_from_history_ref(args.query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_run_loop(args: argparse.Namespace) -> int:
    return run_loop(
        force=args.force,
        dry_run=args.dry_run,
        max_iterations=args.max_iterations,
    )


def _translate_argparse_error(message: str) -> str:
    replacements = (
        ("the following arguments are required: ", "缺少必填参数："),
        ("one of the arguments ", "必须提供其中一个参数："),
        (" is required", ""),
        ("unrecognized arguments: ", "无法识别的参数："),
        ("not allowed with argument ", "不能和以下参数同时使用："),
        ("expected one argument", "需要提供一个参数"),
        ("invalid int value", "不是有效整数"),
        ("invalid choice: ", "不是支持的取值："),
        ("(choose from ", "（可选："),
        ("too few arguments", "参数不足"),
        ("ambiguous option:", "参数不明确："),
        ("argument ", "参数 "),
    )
    translated = message
    for source, target in replacements:
        translated = translated.replace(source, target)
    translated = translated.replace("参数 command:", "参数 命令：")
    translated = translated.replace("：command", "：命令")
    if "（可选：" in translated and translated.endswith(")"):
        translated = f"{translated[:-1]}）"
    return translated


class ChineseArgumentParser(argparse.ArgumentParser):
    HELP_REPLACEMENTS = (
        ("usage:", "用法："),
        ("options:", "选项："),
        ("optional arguments:", "可选参数："),
        ("positional arguments:", "位置参数："),
        ("show this help message and exit", "显示帮助信息后退出"),
    )

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def _translate_help_text(self, text: str) -> str:
        translated = text
        for source, target in self.HELP_REPLACEMENTS:
            translated = translated.replace(source, target)
        return translated

    def format_usage(self) -> str:
        return self._translate_help_text(super().format_usage())

    def format_help(self) -> str:
        return self._translate_help_text(super().format_help())

    def error(self, message: str) -> None:
        self.exit(2, f"{self.prog}: 错误：{_translate_argparse_error(message)}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description="事件情报推送运行时")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=ChineseArgumentParser)

    init_config = sub.add_parser("init-config", help="创建运行时配置模板")
    init_config.add_argument("--force", action="store_true")
    init_config.set_defaults(func=cmd_init_config)

    run_once_parser = sub.add_parser("run-once", help="执行一次事件推送")
    run_once_parser.add_argument("--force", action="store_true")
    run_once_parser.add_argument("--dry-run", action="store_true")
    run_once_parser.add_argument("--quiet", action="store_true")
    run_once_parser.set_defaults(func=cmd_run_once)

    manual_parser = sub.add_parser(
        "manual-push",
        help="手动拉取事件、推送飞书并记录历史",
    )
    manual_parser.add_argument("--keywords", default="", help="多个关键词用逗号、顿号、分号或斜杠分隔；最多 3 个")
    manual_parser.add_argument("--minutes", type=int, default=60)
    manual_parser.add_argument("--page-size", type=int, default=0)
    manual_parser.add_argument("--dry-run", action="store_true")
    manual_parser.add_argument("--no-feishu", action="store_true")
    manual_parser.set_defaults(func=cmd_manual_push)

    run_daily_parser = sub.add_parser("run-daily-summary", help="执行一次每日统计")
    run_daily_parser.add_argument("--force", action="store_true")
    run_daily_parser.add_argument("--dry-run", action="store_true")
    run_daily_parser.add_argument("--quiet", action="store_true")
    run_daily_parser.set_defaults(func=cmd_run_daily)

    key_parser = sub.add_parser("set-api-key", help="保存 deepseekdata API key 到运行时配置")
    key_source = key_parser.add_mutually_exclusive_group(required=True)
    key_source.add_argument("--key", help="API key 值；传 '-' 表示从 stdin 读取")
    key_source.add_argument("--key-file", help="从本地文件读取 API key")
    key_parser.set_defaults(func=cmd_set_event_api_key)

    configure_parser = sub.add_parser("configure", help="保存运行时推送配置")
    active_group = configure_parser.add_mutually_exclusive_group()
    active_group.add_argument("--active", action="store_true", help="开启运行时推送")
    active_group.add_argument("--inactive", action="store_true", help="关闭运行时推送")
    configure_parser.add_argument("--keywords", default=None, help="多个关键词用逗号、顿号、分号或斜杠分隔；最多 3 个")
    configure_parser.add_argument("--schedule", default=None, help=f"可选：{schedule_options_text()}")
    configure_parser.add_argument("--page-size", type=int, default=None)
    configure_parser.add_argument("--daily-summary-time", default=None)
    configure_parser.set_defaults(func=cmd_configure)

    feishu_target_parser = sub.add_parser("set-feishu-target", help="保存飞书自建应用接收目标")
    feishu_target_parser.add_argument("--receive-id", required=True)
    feishu_target_parser.add_argument(
        "--receive-id-type",
        default="chat_id",
        help=f"可选：{feishu_receive_id_type_options_text()}",
    )
    feishu_target_parser.set_defaults(func=cmd_set_feishu_target)

    feishu_webhook_parser = sub.add_parser("set-feishu-webhook", help="保存飞书 Webhook URL")
    webhook_source = feishu_webhook_parser.add_mutually_exclusive_group(required=True)
    webhook_source.add_argument("--url", help="Webhook URL；传 '-' 表示从 stdin 读取")
    webhook_source.add_argument("--url-file", help="从本地文件读取 Webhook URL")
    feishu_webhook_parser.add_argument(
        "--append",
        action="store_true",
        help="追加到现有 Webhook 列表，而不是替换",
    )
    feishu_webhook_parser.set_defaults(func=cmd_set_feishu_webhook)

    install = sub.add_parser("install-schedule", help="安装系统定时任务")
    install.add_argument("--task-name", default="")
    install.set_defaults(func=cmd_install)

    uninstall = sub.add_parser("uninstall-schedule", help="卸载系统定时任务")
    uninstall.add_argument("--task-name", default="")
    uninstall.set_defaults(func=cmd_uninstall)

    status_parser = sub.add_parser("status", help="查看运行时状态")
    status_parser.set_defaults(func=cmd_status)

    detail_parser = sub.add_parser(
        "detail-from-ref",
        help="从最近推送历史中解析用户问法，并调用 deepseekdata 获取事件详情",
    )
    detail_parser.add_argument("--query", required=True, help="用户原始问句，例如：第3条详细看看")
    detail_parser.set_defaults(func=cmd_detail_from_ref)

    run_loop_parser = sub.add_parser("run-loop", help="在当前进程中循环运行")
    run_loop_parser.add_argument("--force", action="store_true")
    run_loop_parser.add_argument("--dry-run", action="store_true")
    run_loop_parser.add_argument("--max-iterations", type=int, default=0)
    run_loop_parser.set_defaults(func=cmd_run_loop)

    return parser


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (TypeError, ValueError):
                pass


def main() -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001
        persist_last_error(str(exc))
        print(
            json.dumps(
                {"status": "error", "detail": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
