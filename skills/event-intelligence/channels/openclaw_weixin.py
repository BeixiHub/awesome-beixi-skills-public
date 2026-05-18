from __future__ import annotations

import json
import shlex
from typing import Any

CHANNEL_ID = "openclaw-weixin"
TEXT_CHUNK_LIMIT = 4000
NO_REPLY = "NO_REPLY"


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
        "delivery_mode": "announce",
        "delivery_channel": CHANNEL_ID,
        "text_chunk_limit": TEXT_CHUNK_LIMIT,
    }


def chunk_text(text: str, limit: int = TEXT_CHUNK_LIMIT) -> list[str]:
    """Split text on line boundaries so each chunk fits Weixin's text limit."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append("".join(current).rstrip())
                current = []
                current_len = 0
            for idx in range(0, len(line), limit):
                chunks.append(line[idx : idx + limit].rstrip())
            continue
        if current and current_len + len(line) > limit:
            chunks.append("".join(current).rstrip())
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current).rstrip())
    return [chunk for chunk in chunks if chunk]


def delivery(cfg: dict[str, Any], *, redacted: bool = False) -> dict[str, str]:
    resolved = target(cfg)
    return {
        "mode": "announce",
        "channel": CHANNEL_ID,
        "to": "<redacted>" if redacted and resolved["to"] else resolved["to"],
        "accountId": "<redacted>" if redacted and resolved["accountId"] else resolved["accountId"],
    }


def delivery_envelope(cfg: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "delivery": delivery(cfg),
        "announce": {
            "type": "message",
            "text": text,
            "chunks": chunk_text(text),
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
    chunks = chunk_text(text)
    safe_envelope = {
        "delivery": delivery(cfg, redacted=True),
        "announce": {
            "type": "message",
            "text": "<redacted>",
            "chunks": len(chunks),
        },
    }
    return [
        {
            "method": "openclaw-announce",
            "channel": CHANNEL_ID,
            "message": "已生成 OpenClaw 微信 announce 内容；必须由 OpenClaw cron/announce 按 delivery 路由投递。",
            "delivery": delivery(cfg, redacted=True),
            "result": {
                "ok": True,
                "envelope": safe_envelope,
                "chunks": len(chunks),
                "text_chunk_limit": TEXT_CHUNK_LIMIT,
            },
        }
    ]


def _cron_agent_message(command: str) -> str:
    return (
        "Run the event-intelligence runtime command below in the workspace. "
        "Use the command output as the entire final answer.\n\n"
        f"Command:\n{command}\n\n"
        "Rules:\n"
        "- If the command output is exactly NO_REPLY, reply exactly NO_REPLY.\n"
        "- Otherwise reply with the command output exactly; add no preface, summary, or markdown wrapper.\n"
        "- Do not use web search or any data source outside this skill."
    )


def _cron_job(
    *,
    name: str,
    schedule_cron: str,
    command: str,
    delivery_cfg: dict[str, str],
    timezone: str,
    timeout_seconds: int,
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
        "delivery": delivery_cfg,
    }


def _openclaw_json_command(job: dict[str, Any]) -> str:
    payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
    return f"openclaw cron add --json {shlex.quote(payload)}"


def openclaw_cron_spec(
    cfg: dict[str, Any],
    *,
    task_name: str,
    command: str,
    schedule_cron: str,
    daily_command: str | None = None,
    daily_cron: str | None = None,
    timezone: str = "Asia/Shanghai",
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    resolved_delivery = delivery(cfg)
    jobs: list[dict[str, Any]] = [
        _cron_job(
            name=f"{task_name}-main",
            schedule_cron=schedule_cron,
            command=command,
            delivery_cfg=resolved_delivery,
            timezone=timezone,
            timeout_seconds=timeout_seconds,
        )
    ]
    if daily_command and daily_cron:
        jobs.append(
            _cron_job(
                name=f"{task_name}-daily",
                schedule_cron=daily_cron,
                command=daily_command,
                delivery_cfg=resolved_delivery,
                timezone=timezone,
                timeout_seconds=timeout_seconds,
            )
        )
    return {
        "scheduler": "openclaw-cron",
        "channel": CHANNEL_ID,
        "delivery_mode": "announce",
        "jobs": jobs,
        "commands": [_openclaw_json_command(job) for job in jobs],
        "json": json.dumps(jobs, ensure_ascii=False, indent=2),
    }


def redact_cron_spec(spec: dict[str, Any]) -> dict[str, Any]:
    safe = json.loads(json.dumps(spec, ensure_ascii=False))
    for job in safe.get("jobs", []) if isinstance(safe.get("jobs"), list) else []:
        job_delivery = job.get("delivery")
        if isinstance(job_delivery, dict):
            if str(job_delivery.get("to", "") or "").strip():
                job_delivery["to"] = "<redacted>"
            if str(job_delivery.get("accountId", "") or "").strip():
                job_delivery["accountId"] = "<redacted>"
    if isinstance(safe.get("commands"), list):
        safe["commands"] = ["<redacted: command contains delivery target>" for _ in safe["commands"]]
    if isinstance(safe.get("json"), str):
        safe["json"] = "<redacted: json contains delivery target>"
    return safe


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
