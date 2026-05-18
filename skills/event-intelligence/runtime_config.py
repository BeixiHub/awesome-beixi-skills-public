from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

EVENT_API_ENV_KEYS = ("EVENT_INTEL_API_KEY", "DEEPSEEKDATA_API_KEY")
EVENT_API_CONFIG_KEYS = (
    "event_intel_api_key",
    "api_key",
    "deepseekdata_api_key",
)
OPENCLAW_EVENT_API_CONFIG_KEYS = (
    "event_intel_api_key",
    "deepseekdata_api_key",
)


def skill_dir() -> Path:
    return Path(__file__).resolve().parent


def project_root_path() -> Path:
    for base in [skill_dir(), *skill_dir().parents]:
        if base.name == "event-intelligence" and (base / "workspace").exists():
            return base
    return skill_dir()


def workspace_root() -> Path:
    for base in [skill_dir(), *skill_dir().parents]:
        if base.name == "workspace":
            return base
    return project_root_path()


def push_config_path() -> Path:
    return skill_dir() / "state" / "push_config.json"


def openclaw_config_paths_from(root: Path) -> list[Path]:
    candidates: list[Path] = []
    explicit = os.getenv("OPENCLAW_CONFIG_PATH", "").strip().strip('"').strip("'")
    if explicit:
        candidates.append(Path(explicit).expanduser())

    for base in [root, *root.parents]:
        candidates.append(base / "openclaw" / "openclaw.json")
        candidates.append(base / "openclaw.json")

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        marker = str(path.resolve()) if path.exists() else str(path)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(path)
    return unique


def openclaw_config_paths() -> list[Path]:
    return openclaw_config_paths_from(project_root_path())


def workspace_openclaw_config_paths() -> list[Path]:
    return openclaw_config_paths_from(workspace_root())


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_openclaw_json(paths: list[Path] | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    for path in paths or openclaw_config_paths():
        if not path.exists():
            continue
        data = read_json(path)
        if isinstance(data, dict):
            return data, path
    return None, None


def normalize_key(key: str) -> str:
    return str(key).replace("_", "").replace("-", "").lower()


def as_nonempty_str(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def split_env_values(value: str | None) -> list[str]:
    if not value:
        return []
    items: list[str] = []
    for chunk in value.replace(";", ",").split(","):
        item = chunk.strip()
        if item:
            items.append(item)
    return items


def _is_feishu_context(path: tuple[str, ...]) -> bool:
    joined = ".".join(path).lower()
    return "feishu" in joined or "lark" in joined


def _infer_receive_type(key_norm: str, value: str) -> str | None:
    if key_norm == "chatid":
        return "chat_id"
    if key_norm == "openid":
        return "open_id"
    if key_norm == "userid":
        return "user_id"
    if key_norm == "unionid":
        return "union_id"
    if key_norm == "email" or "@" in value:
        return "email"
    return None


def _openclaw_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item for item in split_env_values(value)]
    if isinstance(value, list):
        items: list[str] = []
        for child in value:
            items.extend(_openclaw_strings(child))
        return items
    return []


def _collect_openclaw_feishu_config(
    obj: Any,
    path: tuple[str, ...] = (),
) -> dict[str, Any]:
    found: dict[str, Any] = {"webhooks": []}
    if isinstance(obj, list):
        for idx, child in enumerate(obj):
            child_found = _collect_openclaw_feishu_config(child, (*path, str(idx)))
            for key, value in child_found.items():
                if key == "webhooks":
                    found["webhooks"].extend(value)
                elif value and not found.get(key):
                    found[key] = value
        return found

    if not isinstance(obj, dict):
        return found

    context = _is_feishu_context(path)
    for key, value in obj.items():
        key_norm = normalize_key(key)
        value_str = as_nonempty_str(value)
        child_path = (*path, str(key))
        exact_feishu_key = key_norm.startswith("feishu") or key_norm.startswith("lark")

        if value_str:
            if key_norm in {"feishuappid", "larkappid"} or (
                context and key_norm in {"appid", "clientid"}
            ):
                found.setdefault("app_id", value_str)
            elif key_norm in {"feishuappsecret", "larkappsecret"} or (
                context and key_norm in {"appsecret", "clientsecret", "secret"}
            ):
                found.setdefault("app_secret", value_str)
            elif key_norm in {"feishureceiveid", "larkreceiveid"} or (
                context
                and key_norm
                in {"receiveid", "receiverid", "targetid", "chatid", "openid", "userid", "unionid", "email"}
            ):
                found.setdefault("receive_id", value_str)
                inferred = _infer_receive_type(key_norm, value_str)
                if inferred:
                    found.setdefault("receive_id_type", inferred)
            elif key_norm in {"feishureceiveidtype", "larkreceiveidtype"} or (
                context and key_norm in {"receiveidtype", "receiveridtype", "receivetype"}
            ):
                found.setdefault("receive_id_type", value_str)

        if context or exact_feishu_key:
            if key_norm in {"feishuwebhook", "feishuwebhooks", "webhook", "webhookurl", "webhooks"}:
                found["webhooks"].extend(_openclaw_strings(value))

        child_found = _collect_openclaw_feishu_config(value, child_path)
        for child_key, child_value in child_found.items():
            if child_key == "webhooks":
                found["webhooks"].extend(child_value)
            elif child_value and not found.get(child_key):
                found[child_key] = child_value

    return found


def load_openclaw_feishu_config() -> dict[str, Any]:
    data, path = load_openclaw_json()
    if data is None:
        return {"webhooks": []}
    found = _collect_openclaw_feishu_config(data)
    found["webhooks"] = list(dict.fromkeys(found.get("webhooks", [])))
    found["config_path"] = str(path) if path else ""
    return found


def event_api_key_from_config(runtime_config: dict[str, Any] | None = None) -> str:
    raw = runtime_config
    if raw is None:
        path = push_config_path()
        raw = read_json(path) if path.exists() else None
    if not isinstance(raw, dict):
        return ""
    for key in EVENT_API_CONFIG_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _token_from_env_value(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if "=" in text:
            key, raw_token = text.split("=", 1)
            if key.strip() in EVENT_API_ENV_KEYS:
                return raw_token.strip().strip('"').strip("'")
        return ""

    if isinstance(value, dict):
        for key in EVENT_API_ENV_KEYS:
            token = value.get(key)
            if isinstance(token, str) and token.strip():
                return token.strip()
        name = str(value.get("name", "") or value.get("key", "") or "").strip()
        if name in EVENT_API_ENV_KEYS:
            token = value.get("value")
            if isinstance(token, str) and token.strip():
                return token.strip()
        for child in value.values():
            token = _token_from_env_value(child)
            if token:
                return token
        return ""

    if isinstance(value, list):
        for item in value:
            token = _token_from_env_value(item)
            if token:
                return token
    return ""


def find_openclaw_event_api_key(value: Any) -> str:
    if isinstance(value, dict):
        env_token = _token_from_env_value(value.get("env"))
        if env_token:
            return env_token
        exact_key_names = {
            normalize_key(item)
            for item in (*EVENT_API_ENV_KEYS, *OPENCLAW_EVENT_API_CONFIG_KEYS)
        }
        for key, raw_value in value.items():
            key_norm = normalize_key(key)
            if key_norm in exact_key_names:
                token = as_nonempty_str(raw_value)
                if token:
                    return token
        for child in value.values():
            token = find_openclaw_event_api_key(child)
            if token:
                return token
    elif isinstance(value, list):
        for item in value:
            token = find_openclaw_event_api_key(item)
            if token:
                return token
    return ""


def event_api_key_from_openclaw_config() -> str:
    for path in workspace_openclaw_config_paths():
        if not path.exists():
            continue
        token = find_openclaw_event_api_key(read_json(path))
        if token:
            return token
    return ""


def resolve_event_api_key(runtime_config: dict[str, Any] | None = None) -> str:
    """Resolve deepseekdata event API key in the documented order.

    Priority: runtime push_config, process environment, then OpenClaw config.
    """
    for token in (
        event_api_key_from_config(runtime_config),
        os.getenv("EVENT_INTEL_API_KEY", "").strip(),
        os.getenv("DEEPSEEKDATA_API_KEY", "").strip(),
        event_api_key_from_openclaw_config(),
    ):
        if token:
            return token
    return ""
