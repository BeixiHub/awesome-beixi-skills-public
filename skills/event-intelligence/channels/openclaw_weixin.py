from __future__ import annotations

import json
from typing import Any

CHANNEL_ID = "openclaw-weixin"


def _delivery_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    delivery = cfg.get("delivery", {})
    return delivery if isinstance(delivery, dict) else {}


def target(cfg: dict[str, Any]) -> dict[str, str]:
    delivery = _delivery_cfg(cfg)
    to = str(
        delivery.get("to")
        or cfg.get("openclaw_weixin_to")
        or cfg.get("weixin_to")
        or ""
    ).strip()
    account_id = str(
        delivery.get("accountId")
        or delivery.get("account_id")
        or cfg.get("openclaw_weixin_account_id")
        or cfg.get("weixin_account_id")
        or ""
    ).strip()
    return {
        "channel": CHANNEL_ID,
        "to": to,
        "accountId": account_id,
    }


def has_target(cfg: dict[str, Any]) -> bool:
    resolved = target(cfg)
    return bool(resolved["to"] and resolved["accountId"])


def missing_target_message(_: dict[str, Any] | None = None) -> str:
    return (
        "微信 OpenClaw 渠道接收目标未配置。请先完成微信扫码绑定，"
        "并提供 OpenClaw 投递路由：delivery.to（通常形如 xxx@im.wechat）"
        "和 delivery.accountId。自动推送不会回退到临时会话发送。"
    )


def diagnostic(cfg: dict[str, Any]) -> dict[str, Any]:
    resolved = target(cfg)
    return {
        "channel": CHANNEL_ID,
        "has_to": bool(resolved["to"]),
        "has_account_id": bool(resolved["accountId"]),
        "target_configured": bool(resolved["to"] and resolved["accountId"]),
        "delivery_mode": "openclaw-cron-announce",
    }


def delivery_envelope(cfg: dict[str, Any], text: str) -> dict[str, Any]:
    resolved = target(cfg)
    return {
        "delivery": {
            "channel": CHANNEL_ID,
            "to": resolved["to"],
            "accountId": resolved["accountId"],
        },
        "announce": {
            "type": "message",
            "text": text,
        },
    }


def send(cfg: dict[str, Any], text: str, dry_run: bool = False) -> list[dict[str, Any]]:
    if dry_run:
        resolved = target(cfg)
        return [
            {
                "method": "dry-run",
                "channel": CHANNEL_ID,
                "message": "试运行未实际投递微信消息。",
                "delivery": {
                    "channel": CHANNEL_ID,
                    "to": "<redacted>" if resolved["to"] else "",
                    "accountId": "<redacted>" if resolved["accountId"] else "",
                },
                "result": {"ok": True},
            }
        ]
    if not has_target(cfg):
        raise RuntimeError(missing_target_message(cfg))
    envelope = delivery_envelope(cfg, text)
    return [
        {
            "method": "openclaw-announce",
            "channel": CHANNEL_ID,
            "message": "已生成 OpenClaw 微信投递 envelope；由 OpenClaw cron/announce 负责投递。",
            "delivery": {
                "channel": CHANNEL_ID,
                "to": "<redacted>",
                "accountId": "<redacted>",
            },
            "result": {"ok": True, "envelope": envelope},
        }
    ]


def openclaw_cron_spec(cfg: dict[str, Any], *, command: str, schedule_cron: str, daily_command: str | None = None, daily_cron: str | None = None) -> dict[str, Any]:
    resolved = target(cfg)
    jobs: list[dict[str, Any]] = [
        {
            "name": "event-intelligence-main",
            "schedule": schedule_cron,
            "command": command,
            "delivery": resolved,
        }
    ]
    if daily_command and daily_cron:
        jobs.append(
            {
                "name": "event-intelligence-daily",
                "schedule": daily_cron,
                "command": daily_command,
                "delivery": resolved,
            }
        )
    return {
        "scheduler": "openclaw-cron",
        "channel": CHANNEL_ID,
        "jobs": jobs,
        "json": json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2),
    }


def redact_config(safe_cfg: dict[str, Any]) -> dict[str, Any]:
    delivery = safe_cfg.get("delivery")
    if isinstance(delivery, dict):
        delivery = dict(delivery)
        if str(delivery.get("to", "") or "").strip():
            delivery["to"] = "<redacted>"
        if str(delivery.get("accountId", "") or delivery.get("account_id", "") or "").strip():
            delivery["accountId"] = "<redacted>"
            delivery.pop("account_id", None)
        safe_cfg["delivery"] = delivery
    for key in (
        "openclaw_weixin_to",
        "weixin_to",
        "openclaw_weixin_account_id",
        "weixin_account_id",
    ):
        if str(safe_cfg.get(key, "") or "").strip():
            safe_cfg[key] = "<redacted>"
    return safe_cfg
