from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import shlex
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Any, Callable

import event_query as _event_query
from event_query import daily_event_summary_many, list_events, search_events
from runtime_config import (
    resolve_event_api_key,
)

BJT = timezone(timedelta(hours=8))
DATE_FMT = "%Y-%m-%d %H:%M:%S"
ISO_FMT = "%Y-%m-%dT%H:%M:%S+08:00"
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_DIR = SCRIPT_DIR / "state"
PUSH_CONFIG_PATH = STATE_DIR / "push_config.json"
HISTORY_PATH = STATE_DIR / "push_history.json"
DEFAULT_TASK_NAME = "OpenClaw-Event-Intelligence-Push"
OPENCLAW_NO_REPLY = "NO_REPLY"
StreamSink = Callable[[str], None]
MAX_KEYWORDS = 3
FILTER_OBSERVABILITY_OPTIONS = {"off", "metrics", "debug"}
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


def validate_keywords(value: Any, *, default_to_ai: bool = False) -> list[str]:
    keywords = split_keywords(value)
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
        detail = redact_sensitive_text(detail)
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


def normalize_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on", "enable", "enabled"}:
            return True
        if cleaned in {"0", "false", "no", "off", "disable", "disabled"}:
            return False
    if value is None:
        return default
    return bool(value)


def validate_schedule(value: Any) -> str:
    schedule = str(value or "").strip()
    if schedule in SCHEDULE_PRESETS:
        return schedule
    raise RuntimeError(f"暂不支持这个推送时间：{value!r}。当前只支持：{schedule_options_text()}。")


def schedule_lookback_minutes(schedule: str) -> int:
    preset = SCHEDULE_PRESETS[validate_schedule(schedule)]
    return int(preset["lookback_minutes"])


def scheduled_push_lookback_minutes(schedule: str, run_time: datetime | None = None) -> int:
    schedule = validate_schedule(schedule)
    lookback_minutes = schedule_lookback_minutes(schedule)
    current_time = (run_time or now_bjt()).astimezone(BJT)
    if schedule == "24h" and lookback_minutes == 24 * 60 and current_time.weekday() == 0:
        return 3 * 24 * 60
    return lookback_minutes


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
        "keywords": [],
        "event_source": "",
        "event_type": "",
        "is_high_value": "",
        "schedule": DEFAULT_SCHEDULE,
        "page_size": 10,
        "retention_days": 5,
        "daily_summary_time": "09:00",
        "task_name": DEFAULT_TASK_NAME,
        "event_intel_api_key": "",
        "history_file": "state/push_history.json",
        "last_push_time": "",
        "min_verification_comprehensive_score": 0.0,
        "min_semantic_score": 0.35,
        "filter_observability": "off",
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

    cfg["event_source"] = str(cfg.get("event_source", "") or "").strip()
    cfg["event_type"] = str(cfg.get("event_type", "") or "").strip()
    cfg["is_high_value"] = str(cfg.get("is_high_value", "") or "").strip()

    task_name = str(cfg.get("task_name", DEFAULT_TASK_NAME) or DEFAULT_TASK_NAME).strip()
    cfg["task_name"] = task_name or DEFAULT_TASK_NAME

    api_key = str(cfg.get("event_intel_api_key", "") or "").strip()
    cfg["event_intel_api_key"] = api_key
    for key in ("api_key", "deepseekdata_api_key"):
        if key in cfg:
            cfg[key] = str(cfg.get(key, "") or "").strip()

    history_file = str(cfg.get("history_file", "state/push_history.json") or "").strip()
    cfg["history_file"] = history_file or "state/push_history.json"

    def normalize_optional_score(key: str, default: float | None) -> float | None:
        value = cfg.get(key, default)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    cfg["min_verification_comprehensive_score"] = normalize_optional_score(
        "min_verification_comprehensive_score",
        0.0,
    )
    cfg["min_semantic_score"] = normalize_optional_score("min_semantic_score", 0.35)
    observability = str(cfg.get("filter_observability", "off") or "off").strip().lower()
    if observability not in FILTER_OBSERVABILITY_OPTIONS:
        observability = "off"
    cfg["filter_observability"] = observability
    daily_time = str(cfg.get("daily_summary_time", "09:00") or "09:00").strip()
    if not _is_hhmm(daily_time):
        daily_time = "09:00"
    cfg["daily_summary_time"] = daily_time
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


def apply_runtime_filter_observability(cfg: dict[str, Any]) -> None:
    mode = str(cfg.get("filter_observability", "off") or "off").strip().lower()
    if mode not in FILTER_OBSERVABILITY_OPTIONS:
        mode = "off"
    env_name = getattr(_event_query, "FILTER_OBSERVABILITY_ENV", "EVENT_INTEL_FILTER_OBSERVABILITY")
    os.environ[env_name] = mode


def load_and_persist_runtime_config() -> dict[str, Any]:
    """Load config, apply schema/default normalization, then write it back."""
    cfg = load_runtime_config()
    save_json(PUSH_CONFIG_PATH, cfg)
    apply_runtime_event_api_key(cfg, reset_client=True)
    apply_runtime_filter_observability(cfg)
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
    compacted = {
        "eventId": event_id,
        "compliantTitle": title,
        "eventTitle": item.get("eventTitle", ""),
        "eventType": item.get("eventType", ""),
        "eventSource": item.get("eventSource", ""),
        "isHighValue": item.get("isHighValue", ""),
        "eventPublishDate": publish,
        "signalLevel": level,
        "original_summary": original_summary,
        "summary": summary,
        "matched_keywords": matched_keywords,
    }
    for score_key in ("verificationComprehensiveScore", "semanticScore"):
        if score_key in item:
            compacted[score_key] = item.get(score_key)
    for rule_key in ("ruleFilterPassed", "ruleFilterReason", "matchedIncludeTerms", "matchedExcludeTerms"):
        if rule_key in item:
            compacted[rule_key] = item.get(rule_key)
    return compacted


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
    min_verification_comprehensive_score: float | None = 0.0,
    min_semantic_score: float | None = 0.35,
) -> dict[str, Any]:
    keyword_results: list[dict[str, Any]] = []
    if len(keywords) == 1:
        result = search_events(
            keyword=keywords[0],
            minutes=minutes,
            page_size=page_size,
            min_verification_comprehensive_score=min_verification_comprehensive_score,
            min_semantic_score=min_semantic_score,
        )
        keyword_results.append({"keyword": keywords[0], **result})
    else:
        with ThreadPoolExecutor(max_workers=len(keywords)) as executor:
            futures = {
                executor.submit(
                    search_events,
                    keyword=keyword,
                    minutes=minutes,
                    page_size=page_size,
                    min_verification_comprehensive_score=min_verification_comprehensive_score,
                    min_semantic_score=min_semantic_score,
                ): keyword
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


def parse_bjt_datetime(value: str, *, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"{field_name} 不能为空，格式为 YYYY-MM-DD HH:MM:SS。")
    normalized = text.replace("T", " ")
    if len(normalized) == 10:
        normalized = f"{normalized} 00:00:00"
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise RuntimeError(f"{field_name} 格式不正确，请使用 YYYY-MM-DD HH:MM:SS。") from exc
    return parsed.replace(tzinfo=BJT)


def parse_optional_bool(value: Any) -> bool | str | None:
    if value is None:
        return None
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"1", "true", "yes", "y", "on", "是", "高价值"}:
        return True
    if lowered in {"0", "false", "no", "n", "off", "否", "非高价值"}:
        return False
    return text


def normalize_signal_level(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    upper = text.upper()
    aliases = {
        "S": "S级",
        "A": "A级",
        "B": "B级",
        "C": "C级",
        "S级": "S级",
        "A级": "A级",
        "B级": "B级",
        "C级": "C级",
    }
    normalized = aliases.get(upper, aliases.get(text, text))
    if normalized not in SIGNAL_LEVEL_PRIORITY:
        supported = "、".join(SIGNAL_LEVEL_PRIORITY)
        raise RuntimeError(f"signalLevel 只支持：{supported}。")
    return normalized


def filter_events_by_signal_level(events: list[dict[str, Any]], signal_level: str) -> list[dict[str, Any]]:
    if not signal_level:
        return events
    return [
        event
        for event in events
        if str(event.get("signalLevel", "") or "").strip() == signal_level
    ]


def compact_structured_event(item: dict[str, Any]) -> dict[str, Any]:
    event = compact_event(item)
    for key in ("eventTitle", "eventType", "eventSource", "isHighValue"):
        if key in item:
            event[key] = item.get(key)
    for keyword_key in (
        "matched_keywords",
        "semanticScore",
        "ruleFilterPassed",
        "ruleFilterReason",
        "matchedIncludeTerms",
        "matchedExcludeTerms",
    ):
        event.pop(keyword_key, None)
    return event


KEYWORD_FALLBACK_ALIASES: dict[str, tuple[str, ...]] = {
    "ai": ("ai", "aigc", "人工智能", "大模型", "算力", "智能体", "模型"),
}


def _fallback_terms_for_keyword(keyword: str) -> list[str]:
    cleaned = str(keyword or "").strip()
    if not cleaned:
        return []
    terms = [cleaned]
    aliases = KEYWORD_FALLBACK_ALIASES.get(cleaned.casefold(), ())
    for alias in aliases:
        if alias not in terms:
            terms.append(alias)
    return terms


def _fallback_match_text(event: dict[str, Any]) -> str:
    parts = [
        event.get("compliantTitle"),
        event.get("eventTitle"),
        event.get("original_summary"),
        event.get("summary"),
        event.get("eventType"),
        event.get("eventSource"),
    ]
    return "\n".join(str(part or "") for part in parts if part).casefold()


def _structured_keyword_fallback(
    *,
    keywords: list[str],
    minutes: int,
    page_size: int,
    event_source: str = "",
    event_type: str = "",
    is_high_value: Any = None,
) -> dict[str, Any]:
    end_dt = now_bjt()
    start_dt = end_dt - timedelta(minutes=max(1, int(minutes or 60)))
    fallback_page_size = max(1, min(int(page_size or 10), 20))
    fallback_fetch_limit = max(fallback_page_size, min(100, fallback_page_size * 5))
    result = list_events(
        event_publish_date_start=start_dt,
        event_publish_date_end=end_dt,
        event_source=event_source or None,
        event_type=event_type or None,
        is_high_value=parse_optional_bool(is_high_value),
        page_size=fallback_page_size,
        max_events=fallback_fetch_limit,
    )

    matched_events: dict[str, dict[str, Any]] = {}
    totals_by_keyword = {keyword: 0 for keyword in keywords}
    for item in result.get("events", []):
        if not isinstance(item, dict):
            continue
        event = compact_structured_event(item)
        text = _fallback_match_text(event)
        matched_keywords = []
        for keyword in keywords:
            terms = _fallback_terms_for_keyword(keyword)
            if any(term.casefold() in text for term in terms):
                matched_keywords.append(keyword)
        if not matched_keywords:
            continue

        event["matched_keywords"] = matched_keywords
        event["fallback_match"] = "structured_list_keyword_text"
        event_id = str(event.get("eventId", "") or "")
        if not event_id:
            event_id = f"fallback::{event.get('compliantTitle', '')}::{event.get('eventPublishDate', '')}"
        existing = matched_events.get(event_id)
        if existing is None:
            matched_events[event_id] = event
        else:
            existing_matched = existing.setdefault("matched_keywords", [])
            for keyword in matched_keywords:
                if keyword not in existing_matched:
                    existing_matched.append(keyword)

        for keyword in matched_keywords:
            totals_by_keyword[keyword] = totals_by_keyword.get(keyword, 0) + 1

    events = list(matched_events.values())
    events.sort(key=lambda item: str(item.get("eventPublishDate", "") or ""), reverse=True)
    events.sort(key=lambda item: SIGNAL_LEVEL_PRIORITY.get(str(item.get("signalLevel", "") or ""), 99))
    events = events[:page_size]
    return {
        "keywords": keywords,
        "total": len(events),
        "sum_keyword_total": sum(totals_by_keyword.values()),
        "totals_by_keyword": totals_by_keyword,
        "deduped_total": int(result.get("deduped_total", 0) or 0),
        "fetched": int(result.get("fetched", 0) or 0),
        "query": result.get("query", {}),
        "events": events,
    }


def _should_fallback_semantic_error(exc: RuntimeError) -> bool:
    detail = str(exc or "")
    return "deepseekdata API 错误" in detail and "系统异常" in detail


def query_events_unified(
    *,
    keywords: list[str],
    minutes: int,
    page_size: int,
    cfg: dict[str, Any],
    event_source: str = "",
    event_type: str = "",
    is_high_value: Any = None,
    signal_level: str = "",
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
    max_events: int | None = None,
) -> dict[str, Any]:
    cleaned_keywords = validate_keywords(keywords, default_to_ai=False)
    resolved_page_size = max(1, int(page_size or 10))

    if cleaned_keywords:
        fallback_reason = ""
        try:
            result = search_events_for_keywords(
                keywords=cleaned_keywords,
                minutes=minutes,
                page_size=resolved_page_size,
                min_verification_comprehensive_score=cfg.get("min_verification_comprehensive_score"),
                min_semantic_score=cfg.get("min_semantic_score"),
            )
            raw_events = result.get("events", [])
            source = "semantic_event_list"
            mode = "keyword"
        except RuntimeError as exc:
            if not _should_fallback_semantic_error(exc):
                raise
            fallback_reason = redact_sensitive_text(exc)
            result = _structured_keyword_fallback(
                keywords=cleaned_keywords,
                minutes=minutes,
                page_size=resolved_page_size,
                event_source=event_source,
                event_type=event_type,
                is_high_value=is_high_value,
            )
            raw_events = result.get("events", [])
            source = "reportserver_event_analysis_list_keyword_fallback"
            mode = "keyword_fallback"

        response = {
            "mode": mode,
            "source": source,
            "keywords": cleaned_keywords,
            "keyword_label": keywords_label(cleaned_keywords),
            "total": int(result.get("total", 0) or 0),
            "sum_keyword_total": int(result.get("sum_keyword_total", 0) or 0),
            "totals_by_keyword": result.get("totals_by_keyword", {}),
            "events": [compact_event(item) for item in raw_events if isinstance(item, dict)],
        }
        if fallback_reason:
            response["fallback"] = {
                "from": "semantic_event_list",
                "to": "reportserver_event_analysis_list",
                "reason": fallback_reason,
                "match_policy": "keyword text match on platform event title and summaries",
                "fetched": int(result.get("fetched", 0) or 0),
                "query": result.get("query", {}),
            }
            response["query"] = result.get("query", {})
            response["fetched"] = int(result.get("fetched", 0) or 0)
            response["deduped_total"] = int(result.get("deduped_total", 0) or 0)
        return response

    end_dt = end_dt or now_bjt()
    start_dt = start_dt or (end_dt - timedelta(minutes=max(1, int(minutes or 60))))
    result = list_events(
        event_publish_date_start=start_dt,
        event_publish_date_end=end_dt,
        event_source=event_source or None,
        event_type=event_type or None,
        is_high_value=parse_optional_bool(is_high_value),
        signal_levels=signal_level,
        page_size=max(1, min(resolved_page_size, 100)),
        max_events=max_events,
    )
    raw_events = result.get("events", [])
    return {
        "mode": "structured",
        "source": "reportserver_event_analysis_list",
        "keywords": [],
        "keyword_label": "无关键词",
        "total": int(result.get("total", 0) or 0),
        "sum_keyword_total": int(result.get("total", 0) or 0),
        "totals_by_keyword": {},
        "deduped_total": int(result.get("deduped_total", 0) or 0),
        "fetched": int(result.get("fetched", 0) or 0),
        "query": result.get("query", {}),
        "events": [
            compact_structured_event(item)
            for item in raw_events
            if isinstance(item, dict)
        ],
    }


def build_structured_list_text(
    *,
    events: list[dict[str, Any]],
    total: int,
    fetched: int,
    start: str,
    end: str,
    event_source: str = "",
    event_type: str = "",
    is_high_value: Any = None,
    max_items: int | None = None,
) -> str:
    stamp = now_bjt().strftime("%H:%M")
    filters = []
    if event_source:
        filters.append(f"来源「{event_source}」")
    if event_type:
        filters.append(f"类型「{event_type}」")
    if is_high_value is not None and is_high_value != "":
        filters.append(f"高价值={is_high_value}")
    filter_text = "，".join(filters) if filters else "无额外筛选"
    display_limit = max_items if max_items else min(len(events), 30)
    shown = events[:display_limit]
    lines = [
        f"📡 事件列表 ({stamp})",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 时间范围: {start} ~ {end}",
        f"🔎 条件: {filter_text}",
        f"📋 命中 {total} 条，已拉取 {fetched} 条，本次展示 {len(shown)} 条",
        "",
    ]
    for idx, ev in enumerate(shown, start=1):
        title = ev.get("compliantTitle") or ev.get("eventTitle") or "（无标题）"
        source = ev.get("eventSource") or "-"
        event_type_text = ev.get("eventType") or "-"
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"[{idx}] {title}")
        lines.append(
            f"    ⏰ {ev.get('eventPublishDate') or '-'} ｜ 来源: {source} ｜ 类型: {event_type_text}"
        )
        lines.append(f"    📌 一句话总结: {clip(ev.get('original_summary') or ev.get('summary') or '无', 160)}")
        lines.append("")
    if len(events) > len(shown):
        lines.append(f"其余 {len(events) - len(shown)} 条已拉取并记录，可继续按序号查看详情。")
        lines.append("")
    lines.append("如需详情，可直接说「第X条详细看看」。")
    return "\n".join(lines).strip()


def structured_event_list(
    *,
    start: str = "",
    end: str = "",
    minutes: int = 0,
    event_source: str = "",
    event_type: str = "",
    is_high_value: Any = None,
    signal_level: str = "",
    page_size: int = 100,
    limit: int = 0,
    no_delivery: bool = False,
    openclaw_output: bool = False,
    stream_sink: StreamSink | None = None,
) -> dict[str, Any]:
    cfg = load_runtime_config()
    apply_runtime_event_api_key(cfg, reset_client=True)
    retention_days = cfg["retention_days"]
    history = load_history(retention_days=retention_days)
    prune_history(history, retention_days=retention_days)

    if not has_event_api_key(cfg):
        raise RuntimeError(event_api_key_missing_message())

    if start or end:
        if not start or not end:
            raise RuntimeError("按固定时间窗拉取时必须同时提供 start 和 end。")
        start_dt = parse_bjt_datetime(start, field_name="start")
        end_dt = parse_bjt_datetime(end, field_name="end")
    else:
        resolved_minutes = max(1, int(minutes or 60))
        end_dt = now_bjt()
        start_dt = end_dt - timedelta(minutes=resolved_minutes)
    if end_dt < start_dt:
        raise RuntimeError("end 不能早于 start。")

    signal_level = normalize_signal_level(signal_level)
    max_events = int(limit) if int(limit or 0) > 0 else None
    fetch_page_size = min(page_size, 20) if signal_level else page_size
    _emit_stream(
        stream_sink,
        (
            "Event Intelligence: querying ReportServer structured event list, "
            f"window={start_dt.strftime(DATE_FMT)}~{end_dt.strftime(DATE_FMT)}."
        ),
    )
    result = query_events_unified(
        keywords=[],
        minutes=max(1, int(minutes or 60)),
        page_size=fetch_page_size,
        cfg=cfg,
        event_source=event_source,
        event_type=event_type,
        is_high_value=is_high_value,
        signal_level=signal_level,
        start_dt=start_dt,
        end_dt=end_dt,
        max_events=None if signal_level else max_events,
    )
    _emit_stream(
        stream_sink,
        f"Event Intelligence: structured query finished, total={result.get('total', 0)}, fetched={result.get('fetched', 0)}.",
    )

    compacted = [item for item in result.get("events", []) if isinstance(item, dict)]
    compacted = filter_events_by_signal_level(compacted, signal_level)
    filtered_total = len(compacted)
    if signal_level and max_events is not None:
        compacted = compacted[:max_events]
    query = dict(result.get("query", {}) or {})
    if signal_level:
        query["signalLevel"] = signal_level
        result["query"] = query
    run_time = to_iso(now_bjt())
    history["last_run_time"] = run_time
    history["last_error"] = ""

    if not compacted:
        save_history(history)
        return {
            "status": "ok",
            "source": "reportserver_event_analysis_list",
            "total": int(result.get("total", 0) or 0),
            "fetched": int(result.get("fetched", 0) or 0),
            "deduped_total": int(result.get("deduped_total", 0) or 0),
            "filtered_total": filtered_total,
            "pushed": 0,
            "reason": "no_events",
            "message": "没有查询到符合条件的事件。",
            "query": query,
            "run_time": run_time,
            "openclaw_announce_text": OPENCLAW_NO_REPLY if openclaw_output else "",
            "events": [],
        }

    text = build_push_text(
        keyword=result.get("keyword_label") or "无关键词",
        minutes=max(1, int(minutes or 60)),
        events=compacted,
        total=int(result.get("total", 0) or 0),
        title="事件列表",
        max_items=max_events,
        display_limit=max_events or 30,
        totals_by_keyword=result.get("totals_by_keyword", {}),
    )
    indexed_events, batch_time = record_history_batch(
        history,
        keywords=[],
        events=compacted,
        source="reportserver_event_analysis_list_no_delivery" if no_delivery else "reportserver_event_analysis_list",
        retention_days=retention_days,
        update_sent_index=not no_delivery,
        update_last_push_time=not no_delivery,
    )
    save_history(history)
    return {
        "status": "ok",
        "message": f"事件列表查询完成，已处理 {len(indexed_events)} 条事件。",
        "source": "reportserver_event_analysis_list",
        "total": int(result.get("total", 0) or 0),
        "fetched": int(result.get("fetched", 0) or 0),
        "deduped_total": int(result.get("deduped_total", 0) or 0),
        "filtered_total": filtered_total,
        "pushed": len(indexed_events),
        "query": query,
        "run_time": run_time,
        "last_push_time": batch_time if not no_delivery else "",
        "openclaw_announce_text": text if openclaw_output else "",
        "text": text,
        "events": indexed_events,
    }


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    patterns = (
        r"sk-[A-Za-z0-9._-]{6,}",
        r"(?:EVENT_INTEL_API_KEY|DEEPSEEKDATA_API_KEY)\s*=\s*['\"]?[^'\"\s,;]+",
        r"(?i)(event_intel_api_key|deepseekdata_api_key|api_key|apiKey)\s*[:=]\s*['\"]?[^'\"\s,;}]+",
        r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9._-]+",
    )
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, "<redacted>", redacted)
    return redacted


def build_push_text(
    *,
    keyword: str,
    minutes: int,
    events: list[dict[str, Any]],
    total: int,
    title: str = "事件推送",
    max_items: int | None = None,
    display_limit: int | None = None,
    totals_by_keyword: dict[str, int] | None = None,
) -> str:
    stamp = now_bjt().strftime("%H:%M")
    limit = max_items or display_limit or len(events)
    shown_events = events[:display_limit] if display_limit else events
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
    for idx, ev in enumerate(shown_events, start=1):
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
        source = str(ev.get("eventSource", "") or "").strip()
        event_type = str(ev.get("eventType", "") or "").strip()
        if source or event_type:
            lines.append(f"    来源: {source or '-'} ｜ 类型: {event_type or '-'}")
        lines.append(f"    📌 一句话总结: {clip(ev.get('original_summary') or '无', 120)}")
        lines.append(f"    📝 事件摘要: {clip(ev.get('summary') or '无', 180)}")
        lines.append("")
    if len(events) > len(shown_events):
        lines.append(f"其余 {len(events) - len(shown_events)} 条已拉取并记录，可继续按序号查看详情。")
        lines.append("")
    lines.append("如需详情，可直接说「第X条详细看看」。")
    return "\n".join(lines).strip()


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
        ]
    )
    return "\n".join(lines).strip()


def structured_daily_summary(cfg: dict[str, Any], minutes: int = 1440) -> dict[str, Any]:
    end_dt = now_bjt()
    start_dt = end_dt - timedelta(minutes=minutes)
    result = query_events_unified(
        keywords=[],
        minutes=minutes,
        page_size=20,
        cfg=cfg,
        event_source=cfg.get("event_source", ""),
        event_type=cfg.get("event_type", ""),
        is_high_value=cfg.get("is_high_value", ""),
        start_dt=start_dt,
        end_dt=end_dt,
        max_events=None,
    )
    levels = [str(item.get("signalLevel", "") or "") for item in result.get("events", [])]
    total = len(levels)
    s_count = sum(1 for level in levels if level == "S级")
    a_count = sum(1 for level in levels if level == "A级")
    return {
        "window_minutes": minutes,
        "keywords": [],
        "total": total,
        "S级": s_count,
        "A级": a_count,
        "other": total - s_count - a_count,
        "start": start_dt.strftime(DATE_FMT),
        "end": end_dt.strftime(DATE_FMT),
        "source": result.get("source", ""),
        "query": result.get("query", {}),
    }


def has_event_api_key(cfg: dict[str, Any]) -> bool:
    return bool(resolve_event_api_key(cfg))


def event_api_key_missing_message() -> str:
    return (
        "deepseekdata API 凭据未配置。请先配置凭据后再执行查询。"
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
        "message": "deepseekdata API 凭据已配置。",
        "event_api_has_key": True,
    }


def read_secret_file(path_value: str) -> str:
    path = Path(path_value).expanduser()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("读取密钥文件失败。请确认文件存在且当前用户有读取权限。") from exc


def configure_runtime(
    *,
    active: bool | None = None,
    keywords: str | list[str] | None = None,
    event_source: str | None = None,
    event_type: str | None = None,
    is_high_value: str | None = None,
    schedule: str | None = None,
    page_size: int | None = None,
    daily_summary_time: str | None = None,
    filter_observability: str | None = None,
) -> dict[str, Any]:
    cfg = load_runtime_config()
    if active is not None:
        cfg["active"] = active
    if keywords is not None:
        cfg["keywords"] = validate_keywords(keywords, default_to_ai=False)
    if event_source is not None:
        cfg["event_source"] = event_source
    if event_type is not None:
        cfg["event_type"] = event_type
    if is_high_value is not None:
        cfg["is_high_value"] = is_high_value
    if schedule is not None:
        cfg["schedule"] = validate_schedule(schedule)
    if page_size is not None:
        cfg["page_size"] = page_size
    if daily_summary_time is not None:
        cleaned_time = daily_summary_time.strip()
        if cleaned_time:
            cfg["daily_summary_time"] = cleaned_time
    if filter_observability is not None:
        cfg["filter_observability"] = filter_observability

    cfg = normalize_runtime_config(cfg)
    save_json(PUSH_CONFIG_PATH, cfg)
    return {
        "status": "ok",
        "message": schedule_hint_message("已保存运行时推送配置。", cfg["schedule"]),
        "active": cfg["active"],
        "keywords": cfg["keywords"],
        "event_source": cfg.get("event_source", ""),
        "event_type": cfg.get("event_type", ""),
        "is_high_value": cfg.get("is_high_value", ""),
        "schedule": cfg["schedule"],
        "schedule_label": schedule_label(cfg["schedule"]),
        "available_schedules": schedule_options_list(),
        "lookback_minutes": schedule_lookback_minutes(cfg["schedule"]),
        "page_size": cfg["page_size"],
        "daily_summary_time": cfg["daily_summary_time"],
        "filter_observability": cfg["filter_observability"],
    }



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
    compacted = {
        "index": event.get("index"),
        "eventId": str(event.get("eventId", "") or ""),
        "compliantTitle": event.get("compliantTitle", ""),
        "eventPublishDate": event.get("eventPublishDate", ""),
        "signalLevel": event.get("signalLevel", ""),
        "matched_keywords": event.get("matched_keywords", []),
    }
    for key in ("eventSource", "eventType", "isHighValue"):
        if key in event:
            compacted[key] = event.get(key)
    return compacted


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
    keywords_from_label = split_keywords(keyword_label)
    return keywords_from_label[0] if keywords_from_label else ""


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
    if str(resolved.get("batch", {}).get("source", "") or "").startswith("reportserver_event_analysis_list"):
        publish = str(event.get("eventPublishDate", "") or "")
        detail_start = ""
        detail_end = ""
        if publish:
            try:
                publish_dt = parse_bjt_datetime(publish, field_name="eventPublishDate")
                detail_start = (publish_dt - timedelta(minutes=5)).strftime(DATE_FMT)
                detail_end = (publish_dt + timedelta(minutes=5)).strftime(DATE_FMT)
            except RuntimeError:
                detail_start = ""
                detail_end = ""
        detail = _event_query.get_event_detail_from_structured_list(
            event_id=event_id,
            event_publish_date_start=detail_start or None,
            event_publish_date_end=detail_end or None,
            event_source=event.get("eventSource") or None,
            event_type=event.get("eventType") or None,
        )
        if detail:
            return {
                "status": "ok",
                "source": "reportserver_event_analysis_list",
                "message": "已从最近查询记录中匹配，并通过普通事件列表接口获取详情。",
                "eventId": event_id,
                "resolved": {
                    **resolved,
                    "event": compact_batch_event(event),
                },
                "detail": detail,
            }
        return {
            "status": "not_found",
            "reason": "structured_detail_not_found",
            "message": "已在最近查询记录中找到该事件，但普通事件列表接口未返回详情。",
            "resolved": {
                **resolved,
                "event": compact_batch_event(event),
            },
        }
    keyword = str(resolved.get("keyword", "") or "").strip()
    if not keyword:
        return {
            "status": "not_found",
            "reason": "missing_keyword_for_semantic_detail",
            "message": "最近记录中找到了该事件，但缺少关键词，无法调用语义详情接口。",
            "resolved": {
                **resolved,
                "event": compact_batch_event(event),
            },
        }
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



def _emit_stream(sink: StreamSink | None, text: str) -> None:
    if sink is None:
        return
    sink(text)


def _stdout_stream_sink(text: str) -> None:
    print(text, flush=True)


def run_once(
    force: bool = False,
    dry_run: bool = False,
    openclaw_output: bool = False,
    stream_sink: StreamSink | None = None,
) -> dict[str, Any]:
    cfg = load_and_persist_runtime_config()
    _emit_stream(stream_sink, "Event Intelligence: runtime config loaded.")

    retention_days = cfg["retention_days"]
    history = load_history(retention_days=retention_days)
    prune_history(history, retention_days=retention_days)
    _emit_stream(stream_sink, "Event Intelligence: history loaded and pruned.")

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

    lookback_minutes = scheduled_push_lookback_minutes(cfg["schedule"])
    page_size = cfg["page_size"]
    keywords = validate_keywords(cfg.get("keywords"), default_to_ai=False)
    keyword_label = keywords_label(keywords)
    _emit_stream(
        stream_sink,
        f"Event Intelligence: querying deepseekdata for {keyword_label or 'no keyword'}, lookback={lookback_minutes}m, page_size={page_size}.",
    )

    result = query_events_unified(
        keywords=keywords,
        minutes=lookback_minutes,
        page_size=page_size,
        cfg=cfg,
        event_source=cfg.get("event_source", ""),
        event_type=cfg.get("event_type", ""),
        is_high_value=cfg.get("is_high_value", ""),
        max_events=page_size,
    )
    _emit_stream(
        stream_sink,
        f"Event Intelligence: query finished, raw_hits={int(result.get('sum_keyword_total', 0) or 0)}, merged={int(result.get('total', 0) or 0)}.",
    )
    compacted = [item for item in result.get("events", []) if isinstance(item, dict)]

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
        _emit_stream(stream_sink, "Event Intelligence: no new events; skipped delivery.")
        return {
            "status": "ok",
            "total": int(result.get("total", 0) or 0),
            "sum_keyword_total": int(result.get("sum_keyword_total", 0) or 0),
            "totals_by_keyword": result.get("totals_by_keyword", {}),
            "keywords": keywords,
            "source": result.get("source", ""),
            "pushed": 0,
            "reason": "no_new_events",
            "message": "本次没有发现新的事件，不需要推送。",
            "run_time": run_time,
            "lookback_minutes": lookback_minutes,
            "fallback": result.get("fallback", {}),
            "openclaw_announce_text": OPENCLAW_NO_REPLY if openclaw_output else "",
        }

    text = build_push_text(
        keyword=result.get("keyword_label") or keyword_label or "无关键词",
        minutes=lookback_minutes,
        events=new_events,
        total=int(result.get("total", 0) or 0),
        max_items=page_size,
        totals_by_keyword=result.get("totals_by_keyword", {}),
    )
    _emit_stream(stream_sink, f"Event Intelligence: formatted {len(new_events)} new events.")

    _emit_stream(stream_sink, "Event Intelligence: output formatted.")
    real_sent = not dry_run

    history_source = "runtime" if result.get("mode") == "keyword" else "reportserver_event_analysis_list_runtime"
    if not real_sent and result.get("mode") != "keyword":
        history_source = "reportserver_event_analysis_list_runtime_dry_run"
    indexed_events, batch_time = record_history_batch(
        history,
        keywords=keywords,
        events=new_events,
        source=history_source if real_sent else ("runtime_dry_run" if result.get("mode") == "keyword" else history_source),
        retention_days=retention_days,
        update_sent_index=real_sent,
        update_last_push_time=real_sent,
    )
    save_history(history)
    _emit_stream(stream_sink, "Event Intelligence: history saved.")
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
        "source": result.get("source", ""),
        "query": result.get("query", {}),
        "pushed": len(indexed_events),
        "lookback_minutes": lookback_minutes,
        "run_time": run_time,
        "last_push_time": batch_time if real_sent else "",
        "fallback": result.get("fallback", {}),
        "openclaw_announce_text": text if openclaw_output else "",
        "events": indexed_events,
    }


def manual_push(
    *,
    keywords: str | list[str] | None = None,
    minutes: int = 60,
    page_size: int | None = None,
    event_source: str = "",
    event_type: str = "",
    is_high_value: Any = None,
    dry_run: bool = False,
    no_delivery: bool = False,
    openclaw_output: bool = False,
    stream_sink: StreamSink | None = None,
) -> dict[str, Any]:
    cfg = load_runtime_config()
    _emit_stream(stream_sink, "Event Intelligence: runtime config loaded.")
    apply_runtime_event_api_key(cfg, reset_client=True)
    apply_runtime_filter_observability(cfg)
    retention_days = cfg["retention_days"]
    history = load_history(retention_days=retention_days)
    prune_history(history, retention_days=retention_days)
    _emit_stream(stream_sink, "Event Intelligence: history loaded and pruned.")

    if keywords is not None:
        resolved_keywords = validate_keywords(keywords, default_to_ai=False)
    else:
        resolved_keywords = validate_keywords(cfg.get("keywords"), default_to_ai=False)
    resolved_keyword = keywords_label(resolved_keywords)
    resolved_minutes = max(1, int(minutes or 60))
    resolved_page_size = page_size if page_size and page_size > 0 else cfg["page_size"]
    resolved_page_size = max(1, min(int(resolved_page_size), 30))
    resolved_event_source = event_source if event_source != "" else str(cfg.get("event_source", "") or "")
    resolved_event_type = event_type if event_type != "" else str(cfg.get("event_type", "") or "")
    resolved_is_high_value = (
        is_high_value
        if is_high_value not in (None, "")
        else str(cfg.get("is_high_value", "") or "")
    )

    if not has_event_api_key(cfg):
        raise RuntimeError(event_api_key_missing_message())

    _emit_stream(
        stream_sink,
        f"Event Intelligence: querying deepseekdata for {resolved_keyword or 'no keyword'}, lookback={resolved_minutes}m, page_size={resolved_page_size}.",
    )

    result = query_events_unified(
        keywords=resolved_keywords,
        minutes=resolved_minutes,
        page_size=resolved_page_size,
        cfg=cfg,
        event_source=resolved_event_source,
        event_type=resolved_event_type,
        is_high_value=resolved_is_high_value,
        max_events=resolved_page_size,
    )
    _emit_stream(
        stream_sink,
        f"Event Intelligence: query finished, raw_hits={int(result.get('sum_keyword_total', 0) or 0)}, merged={int(result.get('total', 0) or 0)}.",
    )
    compacted = [item for item in result.get("events", []) if isinstance(item, dict)]

    run_time = to_iso(now_bjt())
    history["last_run_time"] = run_time
    history["last_error"] = ""

    if not compacted:
        save_history(history)
        _emit_stream(stream_sink, "Event Intelligence: no events found; history saved.")
        return {
            "status": "ok",
            "source": "manual",
            "total": int(result.get("total", 0) or 0),
            "sum_keyword_total": int(result.get("sum_keyword_total", 0) or 0),
            "totals_by_keyword": result.get("totals_by_keyword", {}),
            "keywords": resolved_keywords,
            "source": result.get("source", ""),
            "pushed": 0,
            "reason": "no_events",
            "message": "没有查询到可推送的事件。",
            "run_time": run_time,
            "fallback": result.get("fallback", {}),
            "openclaw_announce_text": OPENCLAW_NO_REPLY if openclaw_output else "",
            "events": [],
        }

    text = build_push_text(
        keyword=result.get("keyword_label") or resolved_keyword or "无关键词",
        minutes=resolved_minutes,
        events=compacted,
        total=int(result.get("total", 0) or 0),
        title="手动事件推送",
        max_items=resolved_page_size,
        totals_by_keyword=result.get("totals_by_keyword", {}),
    )
    _emit_stream(stream_sink, f"Event Intelligence: formatted {len(compacted)} events.")

    real_sent = not dry_run and not no_delivery

    if result.get("mode") == "keyword":
        history_source = "manual" if real_sent else ("manual_no_delivery" if no_delivery else "manual_dry_run")
    else:
        history_source = (
            "reportserver_event_analysis_list"
            if real_sent
            else ("reportserver_event_analysis_list_no_delivery" if no_delivery else "reportserver_event_analysis_list_dry_run")
        )

    indexed_events, batch_time = record_history_batch(
        history,
        keywords=resolved_keywords,
        events=compacted,
        source=history_source,
        retention_days=retention_days,
        update_sent_index=real_sent,
        update_last_push_time=real_sent,
    )
    save_history(history)
    _emit_stream(stream_sink, "Event Intelligence: history saved.")
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
        "source": result.get("source", ""),
        "query": result.get("query", {}),
        "pushed": len(indexed_events),
        "run_time": run_time,
        "last_push_time": batch_time if real_sent else "",
        "fallback": result.get("fallback", {}),
        "openclaw_announce_text": text if openclaw_output else "",
        "text": text,
        "events": indexed_events,
    }


def run_daily(force: bool = False, dry_run: bool = False, openclaw_output: bool = False) -> dict[str, Any]:
    cfg = load_and_persist_runtime_config()

    if not cfg["active"] and not force:
        return {
            "status": "skipped",
            "reason": "daily_summary_inactive",
            "message": "运行时推送未开启，本次未执行每日统计。",
            "openclaw_announce_text": OPENCLAW_NO_REPLY if openclaw_output else "",
        }

    if not has_event_api_key(cfg):
        raise RuntimeError(event_api_key_missing_message())


    keywords = validate_keywords(cfg.get("keywords"), default_to_ai=False)
    keyword = keywords_label(keywords) if keywords else "无关键词"
    if keywords:
        summary = daily_event_summary_many(keywords=keywords, minutes=1440)
    else:
        summary = structured_daily_summary(cfg=cfg, minutes=1440)
    if int(summary.get("total", 0) or 0) <= 0:
        return {
            "status": "ok",
            "reason": "no_daily_events",
            "message": "过去 24 小时没有查询到可统计的事件。",
            "summary": summary,
            "openclaw_announce_text": OPENCLAW_NO_REPLY if openclaw_output else "",
        }

    text = build_daily_summary_text(keyword_label=keyword, result=summary)
    return {
        "status": "ok",
        "message": "每日事件统计已生成并发送。",
        "summary": summary,
        "openclaw_announce_text": text if openclaw_output else "",
    }


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


def _cron_agent_message(command: str) -> str:
    return (
        "Run the event-intelligence runtime command below immediately in the workspace. "
        "Do not read documentation or explain the workflow. Keep the final answer concise: results plus a brief source note only.\n\n"
        f"Command:\n{command}\n\n"
        "Rules:\n"
        "- If the command output is not exactly NO_REPLY, reply with the command output only; add no preface or markdown wrapper.\n"
        "- If the command output is exactly NO_REPLY, reply exactly NO_REPLY.\n"
        "- Do not repeat status JSON, credential state details, config paths, or inspection steps to the user.\n"
        "- Use only deepseekdata API results and do not supplement empty results from any other source."
    )


def _cron_job(
    *,
    name: str,
    schedule_cron: str,
    command: str,
    timezone: str = "Asia/Shanghai",
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    return {
        "name": name,
        "enabled": True,
        "schedule": {
            "kind": "cron",
            "expr": schedule_cron,
            "tz": timezone,
        },
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "message": _cron_agent_message(command),
            "timeoutSeconds": timeout_seconds,
        },
    }


def _openclaw_json_command(job: dict[str, Any]) -> str:
    payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
    return f"openclaw cron add --json {shlex.quote(payload)}"


def _install_openclaw_cron_jobs(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    openclaw_bin = shutil.which("openclaw")
    if not openclaw_bin:
        return {"attempted": False, "installed": False, "reason": "openclaw_cli_not_found"}

    installed: list[dict[str, Any]] = []
    for job in jobs:
        payload = json.dumps(job, ensure_ascii=False)
        proc = subprocess.run(
            [openclaw_bin, "cron", "add", "--json", payload],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return {
                "attempted": True,
                "installed": False,
                "failed_job": job.get("name", ""),
                "error": (proc.stderr or proc.stdout or "openclaw cron add failed").strip(),
            }
        installed.append({"name": job.get("name", ""), "stdout": proc.stdout.strip()})

    return {"attempted": True, "installed": True, "jobs": installed}


def install_schedule(task_name: str | None = None) -> dict[str, Any]:
    cfg = load_and_persist_runtime_config()
    resolved_name = (task_name or cfg.get("task_name") or DEFAULT_TASK_NAME).strip() or DEFAULT_TASK_NAME

    python_bin = shlex.quote(sys.executable)
    script = shlex.quote(str(Path(__file__).resolve()))
    jobs = [
        _cron_job(
            name=f"{resolved_name}-main",
            schedule_cron=str(SCHEDULE_PRESETS[cfg["schedule"]]["cron"]),
            command=f"{python_bin} {script} run-once --openclaw-output",
        )
    ]
    if cfg["active"]:
        hh, mm = cfg["daily_summary_time"].split(":")
        jobs.append(
            _cron_job(
                name=f"{resolved_name}-daily",
                schedule_cron=f"{int(mm)} {int(hh)} * * *",
                command=f"{python_bin} {script} run-daily-summary --openclaw-output",
            )
        )

    install_result = _install_openclaw_cron_jobs(jobs)
    return {
        "status": "ok",
        "message": (
            "已通过 OpenClaw cron 安装定时任务。"
            if install_result.get("installed")
            else "已生成 OpenClaw cron add 任务规格；当前环境未检测到 openclaw CLI 或安装失败时，可复制 commands 到 OpenClaw 环境执行。"
        ),
        "scheduler": "openclaw-cron",
        "task_name": resolved_name,
        "schedule": cfg["schedule"],
        "schedule_label": schedule_label(cfg["schedule"]),
        "available_schedules": schedule_options_list(),
        "lookback_minutes": schedule_lookback_minutes(cfg["schedule"]),
        "openclaw_cli": install_result,
        "jobs": jobs,
        "commands": [_openclaw_json_command(job) for job in jobs],
    }


def uninstall_schedule(task_name: str | None = None) -> dict[str, Any]:
    cfg = load_runtime_config()
    resolved_name = (task_name or cfg.get("task_name") or DEFAULT_TASK_NAME).strip() or DEFAULT_TASK_NAME
    cfg["active"] = False
    save_json(PUSH_CONFIG_PATH, cfg)
    return {
        "status": "ok",
        "message": "已关闭运行时推送。OpenClaw cron 任务请在 Arkclaw/OpenClaw 平台停用或删除。",
        "scheduler": "openclaw-cron",
        "task_name": resolved_name,
    }


def redacted_runtime_config(cfg: dict[str, Any]) -> dict[str, Any]:
    safe_cfg = dict(cfg)
    for key in ("event_intel_api_key", "api_key", "deepseekdata_api_key"):
        if str(safe_cfg.get(key, "") or "").strip():
            safe_cfg[key] = "<redacted>"
    return safe_cfg


def public_runtime_summary(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "active": cfg["active"],
        "keywords": cfg["keywords"],
        "event_source": cfg.get("event_source", ""),
        "event_type": cfg.get("event_type", ""),
        "is_high_value": cfg.get("is_high_value", ""),
        "schedule": cfg["schedule"],
        "schedule_label": schedule_label(cfg["schedule"]),
        "lookback_minutes": schedule_lookback_minutes(cfg["schedule"]),
        "page_size": cfg["page_size"],
        "daily_summary_time": cfg["daily_summary_time"],
    }


def status() -> dict[str, Any]:
    cfg = load_runtime_config()
    history = load_history(retention_days=cfg["retention_days"])
    prune_history(history, retention_days=cfg["retention_days"])
    latest_batch = history["batches"][0] if history["batches"] else {}
    return {
        "status": "ok",
        "message": schedule_hint_message("运行时状态如下。", cfg["schedule"]),
        "runtime": public_runtime_summary(cfg),
        "event_api": {
            "has_key": has_event_api_key(cfg),
            "detail": "configured" if has_event_api_key(cfg) else "missing",
        },
        "filter_observability": {
            "mode": cfg.get("filter_observability", "off"),
        },
        "history": {
            "batches": len(history.get("batches", [])),
            "last_run_time": history.get("last_run_time", ""),
            "last_push_time": history.get("last_push_time", ""),
            "last_error": redact_sensitive_text(history.get("last_error", "")),
            "latest_batch_time": latest_batch.get("push_time", ""),
            "latest_batch_events": len(latest_batch.get("events", []))
            if isinstance(latest_batch.get("events"), list)
            else 0,
        },
    }


def _filter_metrics_path() -> Path:
    return Path(getattr(_event_query, "RULE_FILTER_METRICS_PATH", STATE_DIR / "rule_filter_metrics.jsonl"))


def _filter_rejections_path() -> Path:
    return Path(getattr(_event_query, "RULE_FILTER_REJECTIONS_PATH", STATE_DIR / "rule_filter_rejections.jsonl"))


def _filter_candidates_path() -> Path:
    return Path(getattr(_event_query, "RULE_FILTER_CANDIDATES_PATH", STATE_DIR / "rule_filter_candidates.jsonl"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except OSError:
        return []
    return rows


def _parse_metric_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BJT)
    return parsed.astimezone(BJT)


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def filter_metrics_summary(*, hours: int = 24, limit_reasons: int = 20) -> dict[str, Any]:
    hours = max(1, int(hours or 24))
    cutoff = now_bjt() - timedelta(hours=hours)
    metrics_path = _filter_metrics_path()
    rejections_path = _filter_rejections_path()

    all_rows = _load_jsonl(metrics_path)
    rows = []
    for row in all_rows:
        ts = _parse_metric_ts(row.get("ts"))
        if ts and ts >= cutoff:
            rows.append(row)

    totals = {
        "runs": len(rows),
        "api_total": 0,
        "fetched_count": 0,
        "score_filtered_count": 0,
        "score_removed_count": 0,
        "rule_filtered_count": 0,
        "rule_removed_count": 0,
        "returned_count": 0,
    }
    by_keyword: dict[str, dict[str, Any]] = {}
    reason_counts: Counter = Counter()
    by_window: dict[str, dict[str, int]] = {}

    for row in rows:
        keyword = str(row.get("keyword", "") or "unknown")
        keyword_bucket = by_keyword.setdefault(
            keyword,
            {
                "runs": 0,
                "api_total": 0,
                "fetched_count": 0,
                "score_filtered_count": 0,
                "score_removed_count": 0,
                "rule_filtered_count": 0,
                "rule_removed_count": 0,
                "returned_count": 0,
            },
        )
        window_key = str(row.get("window_minutes", "") or "unknown")
        window_bucket = by_window.setdefault(
            window_key,
            {
                "runs": 0,
                "score_filtered_count": 0,
                "rule_filtered_count": 0,
                "rule_removed_count": 0,
            },
        )

        keyword_bucket["runs"] += 1
        window_bucket["runs"] += 1
        totals["runs"] = len(rows)
        for key in (
            "api_total",
            "fetched_count",
            "score_filtered_count",
            "score_removed_count",
            "rule_filtered_count",
            "rule_removed_count",
            "returned_count",
        ):
            value = _int_value(row.get(key))
            totals[key] += value
            keyword_bucket[key] += value
            if key in window_bucket:
                window_bucket[key] += value

        raw_reasons = row.get("ruleFilterReason_counts", {})
        if isinstance(raw_reasons, dict):
            for reason, count in raw_reasons.items():
                reason_counts[str(reason)] += _int_value(count)

    rejection_rows = []
    for row in _load_jsonl(rejections_path):
        ts = _parse_metric_ts(row.get("ts"))
        if ts and ts >= cutoff:
            rejection_rows.append(row)

    return {
        "status": "ok",
        "message": f"最近 {hours} 小时 rule filter 观测指标。",
        "window_hours": hours,
        "observability_mode": load_runtime_config().get("filter_observability", "off"),
        "metrics_file": str(metrics_path),
        "rejections_file": str(rejections_path),
        "candidates_file": str(_filter_candidates_path()),
        "totals": totals,
        "by_keyword": [
            {"keyword": keyword, **counts}
            for keyword, counts in sorted(by_keyword.items())
        ],
        "by_window_minutes": [
            {"window_minutes": window, **counts}
            for window, counts in sorted(by_window.items(), key=lambda item: item[0])
        ],
        "ruleFilterReason_distribution": [
            {"reason": reason, "count": count}
            for reason, count in reason_counts.most_common(max(1, limit_reasons))
        ],
        "recent_rejections": [
            {
                "ts": row.get("ts"),
                "keyword": row.get("keyword"),
                "eventId": row.get("eventId"),
                "title": row.get("title"),
                "ruleFilterReason": row.get("ruleFilterReason"),
                "matchedIncludeTerms": row.get("matchedIncludeTerms", []),
                "matchedExcludeTerms": row.get("matchedExcludeTerms", []),
            }
            for row in rejection_rows[-20:]
        ],
    }


def filter_candidates(
    *,
    hours: int = 24,
    keyword: str = "",
    limit_batches: int = 10,
    limit_candidates: int = 200,
) -> dict[str, Any]:
    hours = max(1, int(hours or 24))
    cutoff = now_bjt() - timedelta(hours=hours)
    keyword_filter = str(keyword or "").strip()
    path = _filter_candidates_path()
    batches = []
    candidate_count = 0
    for row in reversed(_load_jsonl(path)):
        ts = _parse_metric_ts(row.get("ts"))
        if not ts or ts < cutoff:
            continue
        if keyword_filter and str(row.get("keyword", "") or "") != keyword_filter:
            continue
        candidates = row.get("candidates", [])
        if not isinstance(candidates, list):
            candidates = []
        remaining = max(0, limit_candidates - candidate_count)
        if remaining <= 0:
            break
        projected_candidates = candidates[:remaining]
        candidate_count += len(projected_candidates)
        batches.append(
            {
                "ts": row.get("ts"),
                "keyword": row.get("keyword"),
                "window_minutes": row.get("window_minutes"),
                "requested_page_size": row.get("requested_page_size"),
                "fetch_size": row.get("fetch_size"),
                "candidate_count": len(candidates),
                "candidates": projected_candidates,
            }
        )
        if len(batches) >= max(1, int(limit_batches or 10)):
            break
    batches.reverse()
    return {
        "status": "ok",
        "message": f"最近 {hours} 小时 filter debug 候选链路。仅在 filter_observability=debug 时产生数据。",
        "window_hours": hours,
        "keyword": keyword_filter,
        "observability_mode": load_runtime_config().get("filter_observability", "off"),
        "candidates_file": str(path),
        "batches": batches,
    }


def clear_filter_observability_files() -> dict[str, Any]:
    paths = [_filter_metrics_path(), _filter_rejections_path(), _filter_candidates_path()]
    removed = []
    missing = []
    errors = []
    for path in paths:
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
            else:
                missing.append(str(path))
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return {
        "status": "ok" if not errors else "partial",
        "message": "过滤观测文件已清理。" if not errors else "部分过滤观测文件清理失败。",
        "removed": removed,
        "missing": missing,
        "errors": errors,
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
                detail = redact_sensitive_text(exc)
                history = load_history(retention_days=cfg["retention_days"])
                history["last_run_time"] = to_iso(now_bjt())
                history["last_error"] = detail
                save_history(history)
                print(
                    json.dumps(
                        {"loop_iteration": iteration, "status": "error", "detail": detail},
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
                "runtime": public_runtime_summary(cfg),
                "event_api": {"has_key": has_event_api_key(cfg)},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_run_once(args: argparse.Namespace) -> int:
    result = run_once(
        force=args.force,
        dry_run=args.dry_run,
        openclaw_output=args.openclaw_output,
        stream_sink=_stdout_stream_sink if args.stream else None,
    )
    if args.openclaw_output:
        print(result.get("openclaw_announce_text") or OPENCLAW_NO_REPLY)
        return 0
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_manual_push(args: argparse.Namespace) -> int:
    result = manual_push(
        keywords=args.keywords,
        minutes=args.minutes,
        page_size=args.page_size or None,
        event_source=args.event_source,
        event_type=args.event_type,
        is_high_value=args.is_high_value,
        dry_run=args.dry_run,
        no_delivery=args.no_delivery,
        openclaw_output=args.openclaw_output,
        stream_sink=_stdout_stream_sink if args.stream else None,
    )
    if args.openclaw_output:
        print(result.get("openclaw_announce_text") or OPENCLAW_NO_REPLY)
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_list_events(args: argparse.Namespace) -> int:
    result = structured_event_list(
        start=args.start,
        end=args.end,
        minutes=args.minutes,
        event_source=args.event_source,
        event_type=args.event_type,
        is_high_value=args.is_high_value,
        signal_level=args.signal_level,
        page_size=args.page_size,
        limit=args.limit,
        no_delivery=args.no_delivery,
        openclaw_output=args.openclaw_output,
        stream_sink=_stdout_stream_sink if args.stream else None,
    )
    if args.openclaw_output:
        print(result.get("openclaw_announce_text") or OPENCLAW_NO_REPLY)
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_run_daily(args: argparse.Namespace) -> int:
    result = run_daily(force=args.force, dry_run=args.dry_run, openclaw_output=args.openclaw_output)
    if args.openclaw_output:
        print(result.get("openclaw_announce_text") or OPENCLAW_NO_REPLY)
        return 0
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
        event_source=args.event_source,
        event_type=args.event_type,
        is_high_value=args.is_high_value,
        schedule=args.schedule,
        page_size=args.page_size,
        daily_summary_time=args.daily_summary_time,
        filter_observability=args.filter_observability,
    )
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


def cmd_metrics_summary(args: argparse.Namespace) -> int:
    hours = args.hours
    if args.days:
        hours = args.days * 24
    result = filter_metrics_summary(hours=hours, limit_reasons=args.limit_reasons)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_filter_candidates(args: argparse.Namespace) -> int:
    hours = args.hours
    if args.days:
        hours = args.days * 24
    result = filter_candidates(
        hours=hours,
        keyword=args.keyword,
        limit_batches=args.limit_batches,
        limit_candidates=args.limit_candidates,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_clear_filter_observability(_: argparse.Namespace) -> int:
    result = clear_filter_observability_files()
    print(json.dumps(result, ensure_ascii=False, indent=2))
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
    run_once_parser.add_argument("--stream", action="store_true", help="print progress updates as soon as each stage starts or finishes")
    run_once_parser.add_argument("--openclaw-output", action="store_true", help=argparse.SUPPRESS)
    run_once_parser.set_defaults(func=cmd_run_once)

    manual_parser = sub.add_parser(
        "manual-push",
        help="\u624b\u52a8\u62c9\u53d6\u4e8b\u4ef6\u5e76\u8bb0\u5f55\u5386\u53f2",
    )
    manual_parser.add_argument("--keywords", default=None, help="多个关键词用逗号、顿号、分号或斜杠分隔；传空字符串表示无关键词")
    manual_parser.add_argument("--minutes", type=int, default=60)
    manual_parser.add_argument("--page-size", type=int, default=0)
    manual_parser.add_argument("--event-source", default="", help="无关键词结构化查询的事件来源，模糊匹配")
    manual_parser.add_argument("--event-type", default="", help="无关键词结构化查询的事件类型")
    manual_parser.add_argument("--is-high-value", default="", help="无关键词结构化查询是否高价值：true/false")
    manual_parser.add_argument("--dry-run", action="store_true")
    manual_parser.add_argument("--no-delivery", action="store_true", help="\u53ea\u67e5\u8be2\u548c\u8bb0\u5f55\u5386\u53f2\uff0c\u4e0d\u6807\u8bb0\u4e3a\u5df2\u63a8\u9001")
    manual_parser.add_argument("--stream", action="store_true", help="print progress updates as soon as each stage starts or finishes")
    manual_parser.add_argument("--openclaw-output", action="store_true", help=argparse.SUPPRESS)
    manual_parser.set_defaults(func=cmd_manual_push)

    list_parser = sub.add_parser(
        "list-events",
        help="按时间窗、来源、事件类型等结构化条件拉取普通事件列表",
    )
    list_parser.add_argument("--start", default="", help="起始时间，格式 YYYY-MM-DD HH:MM:SS")
    list_parser.add_argument("--end", default="", help="结束时间，格式 YYYY-MM-DD HH:MM:SS")
    list_parser.add_argument("--minutes", type=int, default=60, help="未指定 start/end 时使用最近 N 分钟")
    list_parser.add_argument("--event-source", default="", help="事件来源，模糊匹配")
    list_parser.add_argument("--event-type", default="", help="事件类型")
    list_parser.add_argument("--is-high-value", default="", help="是否高价值事件：true/false")
    list_parser.add_argument("--signal-level", default="", help="信号等级筛选，例如 S级、A级、B级、C级；S级事件请使用这个参数，不要使用 isHighValue")
    list_parser.add_argument("--page-size", type=int, default=100, help="分页大小，最大 100")
    list_parser.add_argument("--limit", type=int, default=0, help="最多保留多少条；0 表示按接口 total 全量拉取")
    list_parser.add_argument("--no-delivery", action="store_true", help="只查询和记录历史，不标记为已推送")
    list_parser.add_argument("--stream", action="store_true", help="print progress updates as soon as each stage starts or finishes")
    list_parser.add_argument("--openclaw-output", action="store_true", help=argparse.SUPPRESS)
    list_parser.set_defaults(func=cmd_list_events)

    run_daily_parser = sub.add_parser("run-daily-summary", help="执行一次每日统计")
    run_daily_parser.add_argument("--force", action="store_true")
    run_daily_parser.add_argument("--dry-run", action="store_true")
    run_daily_parser.add_argument("--quiet", action="store_true")
    run_daily_parser.add_argument("--openclaw-output", action="store_true", help=argparse.SUPPRESS)
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
    configure_parser.add_argument("--keywords", default=None, help="多个关键词用逗号、顿号、分号或斜杠分隔；传空字符串表示无关键词")
    configure_parser.add_argument("--event-source", default=None, help="无关键词结构化查询的事件来源，模糊匹配")
    configure_parser.add_argument("--event-type", default=None, help="无关键词结构化查询的事件类型")
    configure_parser.add_argument("--is-high-value", default=None, help="无关键词结构化查询是否高价值：true/false")
    configure_parser.add_argument("--schedule", default=None, help=f"可选：{schedule_options_text()}")
    configure_parser.add_argument("--page-size", type=int, default=None)
    configure_parser.add_argument("--daily-summary-time", default=None)
    configure_parser.add_argument(
        "--filter-observability",
        default=None,
        choices=sorted(FILTER_OBSERVABILITY_OPTIONS),
        help="过滤观测文件写入级别：off 不写入，metrics 只写聚合，debug 写候选链路",
    )
    configure_parser.set_defaults(func=cmd_configure)

    install = sub.add_parser("install-schedule", help="安装系统定时任务")
    install.add_argument("--task-name", default="")
    install.set_defaults(func=cmd_install)

    uninstall = sub.add_parser("uninstall-schedule", help="卸载系统定时任务")
    uninstall.add_argument("--task-name", default="")
    uninstall.set_defaults(func=cmd_uninstall)

    status_parser = sub.add_parser("status", help="查看运行时状态")
    status_parser.set_defaults(func=cmd_status)

    metrics_parser = sub.add_parser("metrics-summary", help="查看规则过滤观测指标")
    metrics_parser.add_argument("--hours", type=int, default=24)
    metrics_parser.add_argument("--days", type=int, default=0)
    metrics_parser.add_argument("--limit-reasons", type=int, default=20)
    metrics_parser.set_defaults(func=cmd_metrics_summary)

    candidates_parser = sub.add_parser("filter-candidates", help="查看规则过滤 debug 候选链路")
    candidates_parser.add_argument("--hours", type=int, default=24)
    candidates_parser.add_argument("--days", type=int, default=0)
    candidates_parser.add_argument("--keyword", default="")
    candidates_parser.add_argument("--limit-batches", type=int, default=10)
    candidates_parser.add_argument("--limit-candidates", type=int, default=200)
    candidates_parser.set_defaults(func=cmd_filter_candidates)

    clear_observability_parser = sub.add_parser("clear-filter-observability", help="清理本地过滤观测文件")
    clear_observability_parser.set_defaults(func=cmd_clear_filter_observability)

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
        detail = redact_sensitive_text(exc)
        persist_last_error(detail)
        print(
            json.dumps(
                {"status": "error", "detail": detail},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
