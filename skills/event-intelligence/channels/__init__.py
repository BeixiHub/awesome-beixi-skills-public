from __future__ import annotations

from typing import Any

from . import feishu, openclaw_weixin

DEFAULT_CHANNEL = openclaw_weixin.CHANNEL_ID
FEISHU_CHANNEL = "feishu"
SUPPORTED_CHANNELS = {DEFAULT_CHANNEL, FEISHU_CHANNEL}


def normalize_channel(value: Any) -> str:
    channel = str(value or "").strip()
    if not channel:
        return DEFAULT_CHANNEL
    aliases = {
        "wechat": DEFAULT_CHANNEL,
        "weixin": DEFAULT_CHANNEL,
        "openclaw_weixin": DEFAULT_CHANNEL,
        "openclaw-wechat": DEFAULT_CHANNEL,
        "lark": FEISHU_CHANNEL,
    }
    channel = aliases.get(channel, channel)
    if channel not in SUPPORTED_CHANNELS:
        raise RuntimeError(
            f"不支持的推送渠道：{channel!r}。当前支持：openclaw-weixin、feishu。"
        )
    return channel


def configured_channel(cfg: dict[str, Any]) -> str:
    delivery = cfg.get("delivery", {})
    delivery_channel = ""
    if isinstance(delivery, dict):
        delivery_channel = str(delivery.get("channel", "") or "").strip()
    return normalize_channel(
        delivery_channel
        or cfg.get("delivery_channel")
        or cfg.get("channel")
        or DEFAULT_CHANNEL
    )


def diagnostic(cfg: dict[str, Any]) -> dict[str, Any]:
    channel = configured_channel(cfg)
    if channel == FEISHU_CHANNEL:
        detail = feishu.diagnostic(cfg)
    else:
        detail = openclaw_weixin.diagnostic(cfg)
    return {
        "channel": channel,
        "supported_channels": sorted(SUPPORTED_CHANNELS),
        "active_channel": detail,
        "feishu": feishu.diagnostic(cfg),
        "openclaw_weixin": openclaw_weixin.diagnostic(cfg),
    }


def has_target(cfg: dict[str, Any]) -> bool:
    channel = configured_channel(cfg)
    if channel == FEISHU_CHANNEL:
        return feishu.has_target(cfg)
    return openclaw_weixin.has_target(cfg)


def missing_target_message(cfg: dict[str, Any]) -> str:
    channel = configured_channel(cfg)
    if channel == FEISHU_CHANNEL:
        return feishu.missing_target_message(cfg)
    return openclaw_weixin.missing_target_message(cfg)


def send(cfg: dict[str, Any], text: str, dry_run: bool = False) -> list[dict[str, Any]]:
    channel = configured_channel(cfg)
    if channel == FEISHU_CHANNEL:
        return feishu.send(cfg, text, dry_run=dry_run)
    return openclaw_weixin.send(cfg, text, dry_run=dry_run)


def redact_config(cfg: dict[str, Any]) -> dict[str, Any]:
    safe = feishu.redact_config(dict(cfg))
    safe = openclaw_weixin.redact_config(safe)
    return safe
