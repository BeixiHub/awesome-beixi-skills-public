from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from runtime_config import load_openclaw_feishu_config, split_env_values

VALID_RECEIVE_ID_TYPES = {"chat_id", "open_id", "user_id", "union_id", "email"}


def receive_id_type_options_text() -> str:
    return "、".join(sorted(VALID_RECEIVE_ID_TYPES))


def build_interactive_card(text: str) -> dict[str, Any]:
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


def post_webhook(webhook: str, text: str) -> dict[str, Any]:
    payload = {"msg_type": "interactive", "card": build_interactive_card(text)}
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
        if not url or not url.startswith("https://"):
            continue
        if "replace-with-your-webhook" in url:
            continue
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


def resolve_app_config(cfg: dict[str, Any] | None = None) -> dict[str, str]:
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
    if receive_id_type not in VALID_RECEIVE_ID_TYPES:
        receive_id_type = ""
    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "receive_id": receive_id,
        "receive_id_type": receive_id_type,
        "config_path": str(openclaw_cfg.get("config_path", "") or ""),
    }


def resolve_app_target(cfg: dict[str, Any] | None = None) -> dict[str, str] | None:
    target = resolve_app_config(cfg)
    if (
        not target.get("app_id")
        or not target.get("app_secret")
        or not target.get("receive_id")
        or target.get("receive_id_type") not in VALID_RECEIVE_ID_TYPES
    ):
        return None
    return target


def diagnostic(cfg: dict[str, Any]) -> dict[str, Any]:
    app_cfg = resolve_app_config(cfg)
    return {
        "webhook_count": len(resolve_webhooks(cfg)),
        "has_app_id": bool(app_cfg["app_id"]),
        "has_app_secret": bool(app_cfg["app_secret"]),
        "has_receive_id": bool(app_cfg["receive_id"]),
        "receive_id_type": app_cfg["receive_id_type"] if app_cfg["receive_id"] else "",
        "receive_id_configured_in_runtime": bool(str(cfg.get("feishu_receive_id", "") or "").strip()),
        "openclaw_config_path": app_cfg.get("config_path", ""),
    }


def has_target(cfg: dict[str, Any]) -> bool:
    return bool(resolve_webhooks(cfg) or resolve_app_target(cfg))


def missing_target_message(cfg: dict[str, Any] | None = None) -> str:
    app_cfg = resolve_app_config(cfg)
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


def api_base() -> str:
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


def fetch_tenant_access_token(app_id: str, app_secret: str) -> str:
    parsed = post_json_request(
        f"{api_base()}/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        {"Content-Type": "application/json; charset=utf-8"},
    )
    if parsed.get("code") != 0:
        raise RuntimeError(f"飞书 tenant_access_token 获取失败：{parsed}")
    token = str(parsed.get("tenant_access_token", "") or "")
    if not token:
        raise RuntimeError(f"飞书 token 响应缺少 tenant_access_token：{parsed}")
    return token


def post_app(target: dict[str, str], text: str) -> dict[str, Any]:
    receive_id_type = target.get("receive_id_type", "")
    if receive_id_type not in VALID_RECEIVE_ID_TYPES:
        raise RuntimeError(
            f"不支持的飞书 receive_id_type：{receive_id_type!r}。"
            f"可选值：{receive_id_type_options_text()}。"
        )
    token = fetch_tenant_access_token(target["app_id"], target["app_secret"])
    content = json.dumps(build_interactive_card(text), ensure_ascii=False)
    parsed = post_json_request(
        f"{api_base()}/im/v1/messages?receive_id_type={receive_id_type}",
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


def send(cfg: dict[str, Any], text: str, dry_run: bool = False) -> list[dict[str, Any]]:
    if dry_run:
        return [{"method": "dry-run", "message": "试运行未实际发送飞书消息。", "result": {"ok": True}}]

    webhooks = resolve_webhooks(cfg)
    if webhooks:
        results: list[dict[str, Any]] = []
        for url in webhooks:
            try:
                result = post_webhook(url, text)
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

    app_target = resolve_app_target(cfg)
    if app_target:
        receive_id_type = app_target.get("receive_id_type", "chat_id")
        receive_id = app_target.get("receive_id", "")
        return [
            {
                "method": "app",
                "message": "飞书自建应用推送成功。",
                "receive_id_type": receive_id_type,
                "receive_id": "<redacted>" if receive_id else "",
                "result": post_app(app_target, text),
            }
        ]

    raise RuntimeError(missing_target_message(cfg))


def redact_config(safe_cfg: dict[str, Any]) -> dict[str, Any]:
    webhooks = safe_cfg.get("feishu_webhooks")
    if isinstance(webhooks, list):
        safe_cfg["feishu_webhooks"] = ["<redacted>" for item in webhooks if str(item or "").strip()]
    elif str(webhooks or "").strip():
        safe_cfg["feishu_webhooks"] = ["<redacted>"]
    if str(safe_cfg.get("feishu_receive_id", "") or "").strip():
        safe_cfg["feishu_receive_id"] = "<redacted>"
    return safe_cfg
