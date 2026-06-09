from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import event_query
import push_runtime


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("tool payload must be a JSON object")
    return data


def apply_runtime() -> dict[str, Any]:
    cfg = push_runtime.load_runtime_config()
    if not push_runtime.has_event_api_key(cfg):
        raise RuntimeError(push_runtime.event_api_key_missing_message())
    push_runtime.apply_runtime_event_api_key(cfg, reset_client=True)
    push_runtime.apply_runtime_filter_observability(cfg)
    return cfg


def semantic_detail(payload: dict[str, Any]) -> dict[str, Any]:
    apply_runtime()
    event_id = str(payload.get("eventId", "") or "").strip()
    keyword = str(payload.get("keyword", "") or "").strip()
    if not event_id or not keyword:
        raise RuntimeError("semantic detail requires eventId and keyword")
    detail = event_query.get_event_detail(keyword=keyword, event_id=event_id)
    if not detail:
        return {
            "status": "not_found",
            "source": "semantic_event_list",
            "eventId": event_id,
            "keyword": keyword,
            "detail": None,
        }
    return {
        "status": "ok",
        "source": "semantic_event_list",
        "eventId": event_id,
        "keyword": keyword,
        "detail": detail,
    }


def structured_detail(payload: dict[str, Any]) -> dict[str, Any]:
    apply_runtime()
    event_id = str(payload.get("eventId", "") or "").strip()
    if not event_id:
        raise RuntimeError("structured detail requires eventId")
    detail = event_query.get_event_detail_from_structured_list(
        event_id=event_id,
        event_publish_date_start=str(payload.get("start", "") or "").strip() or None,
        event_publish_date_end=str(payload.get("end", "") or "").strip() or None,
        event_source=str(payload.get("eventSource", "") or "").strip() or None,
        event_type=str(payload.get("eventType", "") or "").strip() or None,
        is_high_value=payload.get("isHighValue"),
    )
    if not detail:
        return {
            "status": "not_found",
            "source": "reportserver_event_analysis_list",
            "eventId": event_id,
            "detail": None,
        }
    return {
        "status": "ok",
        "source": "reportserver_event_analysis_list",
        "eventId": event_id,
        "detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("semantic-detail", "structured-detail"))
    args = parser.parse_args()
    try:
        payload = read_payload()
        if args.command == "semantic-detail":
            result = semantic_detail(payload)
        else:
            result = structured_detail(payload)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"status": "error", "detail": push_runtime.redact_sensitive_text(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
