from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from env_loader import load_skill_env
from backend_client import build_backend_client
from market_data_client import MarketDataClient
from niuguwang_client import build_niuguwang_client

load_skill_env()


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT_DIR / "state"
BINDING_STATE_PATH = STATE_DIR / "binding_state.json"
INNER_CODE_CACHE_PATH = STATE_DIR / "inner_code_cache.json"
UTC = timezone.utc
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def save_json(path: Path, payload: Any) -> None:
    ensure_state_dir()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_binding_state() -> dict[str, Any]:
    raw = load_json(BINDING_STATE_PATH, {})
    if not isinstance(raw, dict):
        raw = {}

    def text_field(key: str, default: str = "") -> str:
        return str(raw.get(key) or default).strip()

    return {
        "claw_token": text_field("claw_token"),
        "real_name": text_field("real_name"),
        "phone": text_field("phone"),
        "masked_phone": text_field("masked_phone"),
        "registration_status": text_field("registration_status", "not_started"),
        "binding_status": text_field("binding_status", "unbound"),
        "account_id": raw.get("account_id"),
        "user_name": text_field("user_name"),
        "can_trade": bool(raw.get("can_trade", False)),
        "registration_source": text_field("registration_source"),
        "updated_at": text_field("updated_at"),
    }


def save_binding_state(state: dict[str, Any]) -> None:
    snapshot = load_binding_state()
    snapshot.update(state)
    snapshot["updated_at"] = now_iso()
    save_json(BINDING_STATE_PATH, snapshot)


def load_inner_code_cache() -> dict[str, Any]:
    raw = load_json(INNER_CODE_CACHE_PATH, {})
    return raw if isinstance(raw, dict) else {}


def save_inner_code_cache(cache: dict[str, Any]) -> None:
    save_json(INNER_CODE_CACHE_PATH, cache)


def emit(status: str, op: str, summary: str, *, data: Any = None, next_action: str | None = None, **extra: Any) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "op": op,
        "summary": summary,
        "data": data,
    }
    if next_action:
        payload["next_action"] = next_action
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def ok(op: str, summary: str, *, data: Any = None, next_action: str | None = None, **extra: Any) -> None:
    emit("ok", op, summary, data=data, next_action=next_action, **extra)


def error(op: str, summary: str, *, data: Any = None, next_action: str | None = None, **extra: Any) -> None:
    emit("error", op, summary, data=data, next_action=next_action, **extra)


def get_claw_token(arg_value: str | None) -> str:
    token = (arg_value or "").strip()
    if token:
        return token
    return load_binding_state().get("claw_token", "")


def mask_phone(phone: str) -> str:
    if len(phone) != 11:
        return phone
    return phone[:3] + "****" + phone[-4:]


def normalize_phone(phone: str) -> str:
    normalized = phone.strip()
    normalized = re.sub(r"[\s\-()]", "", normalized)
    if normalized.startswith("+86"):
        normalized = normalized[3:]
    elif normalized.startswith("86") and len(normalized) == 13 and normalized[2] == "1":
        normalized = normalized[2:]
    return normalized


def validate_phone(phone: str) -> str:
    phone = normalize_phone(phone)
    if not phone.isdigit():
        raise RuntimeError("手机号格式不正确，请提供 11 位大陆手机号。")
    if len(phone) != 11:
        raise RuntimeError("手机号位数不对，请提供 11 位大陆手机号。")
    if not PHONE_RE.match(phone):
        raise RuntimeError("手机号格式不合法，请提供有效的大陆手机号。")
    if len(set(phone)) == 1:
        raise RuntimeError("手机号看起来不正确，请重新确认后再发给我。")
    return phone


def validate_real_name(real_name: str) -> str:
    real_name = real_name.strip()
    if len(real_name) < 2:
        raise RuntimeError("姓名过短，请提供真实姓名。")
    return real_name


def validate_quantity(quantity: int) -> int:
    if quantity <= 0:
        raise RuntimeError("数量必须大于 0。")
    return quantity


def validate_price(price: float) -> float:
    if price <= 0:
        raise RuntimeError("价格必须大于 0。")
    return price


def require_backend_client():
    if not os.getenv("PAPER_TRADING_BACKEND_BASE_URL", "").strip():
        raise RuntimeError("当前未配置模拟盘后端地址。")
    return build_backend_client()


def binding_phase(state: dict[str, Any]) -> str:
    if state.get("can_trade"):
        return "active"
    if state.get("binding_status") == "active":
        return "active"
    if state.get("registration_status") in {"submitting", "registering", "registered"}:
        return "registering"
    return "unbound"


def parse_query_success(payload: Any) -> bool:
    return bool(isinstance(payload, dict) and payload.get("code") == 0 and payload.get("success") is True)


def parse_register_success(payload: Any) -> bool:
    return bool(isinstance(payload, dict) and payload.get("code") == 0 and payload.get("success") is True)


def parse_order_success(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    message = str(payload.get("message", ""))
    if payload.get("code") != 0:
        return False
    keywords = ("委托成功", "资金冻结", "卖出", "买入")
    return any(keyword in message for keyword in keywords)


def parse_cancel_success(payload: Any, refreshed_payload: Any, delegate_id: str) -> bool:
    if isinstance(payload, dict) and payload.get("code") == 0 and payload.get("success") is True:
        return True
    if not isinstance(refreshed_payload, dict):
        return False
    data = refreshed_payload.get("data")
    if isinstance(data, list):
        for item in data:
            current_id = str(item.get("delegateID") or item.get("delegateId") or item.get("id") or "")
            state = str(item.get("delegateState") or item.get("state") or "")
            if current_id == str(delegate_id) and state in {"2", "已撤单"}:
                return True
    return False


def parse_order_failure(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if parse_order_success(payload):
        return False
    code = payload.get("code")
    message = str(payload.get("message", "")).strip()
    if code not in (0, None):
        return True
    failure_keywords = ("不足", "失败", "异常", "错误", "无效", "不存在", "不可用")
    return any(keyword in message for keyword in failure_keywords)


def describe_delegate_state(state: Any) -> str:
    mapping = {
        "0": "待成交",
        "1": "已成交",
        "2": "已撤销或废单",
        "3": "当前不是待成交状态",
    }
    return mapping.get(str(state).strip(), f"状态码 {state}")


def find_delegate_item(payload: Any, delegate_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        current_id = str(item.get("delegateID") or item.get("delegateId") or item.get("id") or "").strip()
        if current_id == str(delegate_id).strip():
            return item
    return None


def fetch_delegates_with_retry(
    claw_token: str,
    *,
    attempts: int = 3,
    delay_seconds: float = 0.8,
) -> Any:
    last_payload: Any = None
    client = build_niuguwang_client()
    for attempt in range(attempts):
        last_payload = client.get_today_delegate_list(claw_token)
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    return last_payload


def build_local_claw_token(existing_value: str | None = None) -> str:
    token = str(existing_value or "").strip()
    if token:
        return token
    return f"pt_local_{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"


def resolve_symbol(identifier: str) -> dict[str, Any]:
    return MarketDataClient().lookup_security_profile(identifier)


def resolve_quote(identifier: str) -> dict[str, Any]:
    symbol_result = resolve_symbol(identifier)
    profile = symbol_result.get("profile")
    if profile:
        quote = MarketDataClient().get_realtime_quote(profile["ticker"])
        return {"identifier": identifier, "profile": profile, "quote": quote}
    if identifier.isdigit() and len(identifier) == 6:
        quote = MarketDataClient().get_realtime_quote(identifier)
        return {"identifier": identifier, "profile": {"ticker": identifier, "name": ""}, "quote": quote}
    return {
        "identifier": identifier,
        "profile": None,
        "quote": None,
        "candidates": symbol_result.get("candidates", []),
    }


def update_cache_from_payload(cache: dict[str, Any], payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        for item in data["items"]:
            remember_mapping(cache, item)
    if isinstance(data, list):
        for item in data:
            remember_mapping(cache, item)


def remember_mapping(cache: dict[str, Any], item: dict[str, Any]) -> None:
    stock_code = str(item.get("stockCode") or item.get("stock_code") or "").strip()
    stock_name = str(item.get("stockName") or item.get("stock_name") or "").strip()
    inner_code = item.get("innerCode") or item.get("inner_code")
    if not stock_code or inner_code in (None, ""):
        return
    mapping = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "inner_code": int(inner_code),
    }
    cache[stock_code] = mapping
    if stock_name:
        cache[stock_name] = mapping


def resolve_inner_code(identifier: str, claw_token: str | None) -> dict[str, Any]:
    cache = load_inner_code_cache()
    cached = cache.get(identifier)
    if isinstance(cached, dict):
        return {"source": "cache", **cached}

    symbol_result = resolve_symbol(identifier)
    profile = symbol_result.get("profile")
    if profile:
        cached = cache.get(profile["ticker"])
        if isinstance(cached, dict):
            return {"source": "cache", **cached}

    if claw_token:
        client = build_niuguwang_client()
        for payload in (
            client.get_stock_holding_list(claw_token),
            client.get_today_delegate_list(claw_token),
            client.get_history_delegate_list(claw_token),
        ):
            update_cache_from_payload(cache, payload)
        save_inner_code_cache(cache)
        candidate_keys = [identifier]
        if profile:
            candidate_keys.extend([profile["ticker"], profile["name"]])
        for key in candidate_keys:
            value = cache.get(key)
            if isinstance(value, dict):
                return {"source": "runtime", **value}

    return {
        "source": "unresolved",
        "identifier": identifier,
        "message": "未找到 innerCode，请先补充 innerCode，或先查询一次持仓/委托让系统自动回填。",
        "candidates": symbol_result.get("candidates", []),
    }


def sync_runtime_status(claw_token: str) -> dict[str, Any]:
    payload = build_niuguwang_client().get_account(claw_token)
    if parse_query_success(payload):
        data = payload.get("data") or {}
        state = {
            "claw_token": claw_token,
            "binding_status": "active",
            "registration_status": "registered",
            "can_trade": True,
            "account_id": data.get("accountId"),
            "user_name": data.get("userName", ""),
        }
        save_binding_state(state)
        return state
    return {
        "claw_token": claw_token,
        "binding_status": "unknown",
        "registration_status": "unknown",
        "can_trade": False,
        "raw": payload,
    }


def collect_environment() -> dict[str, Any]:
    state = load_binding_state()
    return {
        "niuguwangBaseUrl": os.getenv("PAPER_TRADING_NGW_BASE_URL", "https://apicore.niuguwang.com").strip(),
        "statePath": str(BINDING_STATE_PATH),
        "innerCodeCachePath": str(INNER_CODE_CACHE_PATH),
        "bindingPhase": binding_phase(state),
        "localState": state,
        "localDirectRegistration": True,
    }


def require_claw_token(claw_token: str) -> str:
    if not claw_token:
        raise RuntimeError("当前还没有可用的模拟盘账户，请先注册。")
    return claw_token


def cmd_doctor(_: argparse.Namespace) -> None:
    env = collect_environment()
    ok(
        "doctor",
        "环境已就绪。",
        data=env,
        next_action="你可以先查看当前状态；如果还没注册，就直接提供手机号让我帮你开通模拟盘。",
    )


def cmd_status(args: argparse.Namespace) -> None:
    claw_token = get_claw_token(args.claw_token)
    state = load_binding_state()
    if not claw_token:
        result = {
            "clawToken": "",
            "bindingPhase": binding_phase(state),
            "bindingStatus": state["binding_status"],
            "registrationStatus": state["registration_status"],
            "canTrade": False,
        }
        ok(
            "status",
            "当前还没有绑定模拟盘账户。",
            data=result,
            next_action="直接把手机号发给我，我可以帮你开通模拟盘；如果你愿意，也可以顺带告诉我姓名。",
        )
        return

    runtime_state = sync_runtime_status(claw_token)
    next_action = "现在可以继续查账户、查持仓，或者直接买卖交易。" if runtime_state.get("can_trade") else "当前还没确认可交易状态，你可以稍后再查一次。"
    ok("status", "已同步当前模拟盘状态。", data=runtime_state, next_action=next_action)


def cmd_register(args: argparse.Namespace) -> None:
    phone = validate_phone(args.phone)
    real_name = validate_real_name(args.real_name or f"模拟盘用户{phone[-4:]}")
    existing_claw_token = get_claw_token(args.claw_token)

    if getattr(args, "via_backend", False):
        payload = require_backend_client().register(real_name, phone, existing_claw_token)
        data = payload.get("data") if isinstance(payload, dict) else {}
        if args.save_state and isinstance(data, dict):
            save_binding_state(
                {
                    "claw_token": str(data.get("clawToken", existing_claw_token)).strip(),
                    "real_name": real_name,
                    "phone": phone,
                    "masked_phone": str(data.get("maskedPhone", mask_phone(phone))).strip(),
                    "registration_status": str(data.get("registrationStatus", "registered")).strip(),
                    "binding_status": str(data.get("bindingStatus", "active")).strip(),
                    "account_id": data.get("accountId"),
                    "user_name": str(data.get("userName", "")).strip(),
                    "can_trade": bool(data.get("canTrade") or data.get("bindingStatus") == "active"),
                    "registration_source": "backend",
                }
            )
        can_trade = bool(isinstance(data, dict) and (data.get("canTrade") or data.get("bindingStatus") == "active"))
        next_action = "现在可以继续查账户、查持仓，或者直接买卖交易。" if can_trade else "开户请求已提交，你可以稍后再查一次状态。"
        if isinstance(payload, dict) and payload.get("success") is False:
            error("register", str(payload.get("message") or "模拟盘开户失败。"), data=payload, next_action=next_action)
            return
        ok("register", "模拟盘开户请求已提交。", data=payload, next_action=next_action)
        return

    # Direct local registration must always mint a fresh claw token.
    claw_token = build_local_claw_token()
    register_payload = build_niuguwang_client().register(claw_token, phone)
    account_payload = build_niuguwang_client().get_account(claw_token)
    account_data = account_payload.get("data") if isinstance(account_payload, dict) else {}
    register_success = parse_register_success(register_payload)
    account_success = parse_query_success(account_payload)

    state_update = {
        "claw_token": claw_token,
        "real_name": real_name,
        "phone": phone,
        "masked_phone": mask_phone(phone),
        "registration_status": "registered" if register_success else "registering",
        "binding_status": "active" if account_success else "registering",
        "account_id": account_data.get("accountId") if isinstance(account_data, dict) else None,
        "user_name": str(account_data.get("userName", "")).strip() if isinstance(account_data, dict) else "",
        "can_trade": account_success,
        "registration_source": "local",
    }
    if args.save_state:
        save_binding_state(state_update)

    combined_payload = {
        "mode": "local",
        "data": {
            "clawToken": claw_token,
            "maskedPhone": mask_phone(phone),
            "registrationStatus": state_update["registration_status"],
            "bindingStatus": state_update["binding_status"],
            "accountId": state_update["account_id"],
            "userName": state_update["user_name"],
            "canTrade": state_update["can_trade"],
        },
        "registerPayload": register_payload,
        "accountPayload": account_payload,
    }

    if account_success:
        ok("register", "模拟盘已经开通，现在可以开始交易。", data=combined_payload, next_action="现在可以继续查账户、查持仓，或者直接买卖交易。")
        return
    if register_success:
        error("register", "开户请求已提交，但暂时还没确认可交易状态。", data=combined_payload, next_action="你可以稍后再查一次状态。")
        return
    error(
        "register",
        str(register_payload.get("message") if isinstance(register_payload, dict) else "模拟盘开户失败。"),
        data=combined_payload,
        next_action="请确认手机号是否正确，或稍后再试一次。",
    )


def cmd_account(args: argparse.Namespace) -> None:
    claw_token = require_claw_token(get_claw_token(args.claw_token))
    payload = build_niuguwang_client().get_account(claw_token)
    if parse_query_success(payload):
        data = payload.get("data") or {}
        save_binding_state(
            {
                "claw_token": claw_token,
                "account_id": data.get("accountId"),
                "user_name": data.get("userName", ""),
                "can_trade": True,
            }
        )
        ok("account", "账户信息查询成功。", data=payload, next_action="你可以继续查看持仓，或者直接告诉我要买卖哪只股票。")
        return
    error("account", "账户信息查询失败。", data=payload, next_action="可以稍后再试，或者先重新检查当前模拟盘状态。")


def cmd_holdings(args: argparse.Namespace) -> None:
    claw_token = require_claw_token(get_claw_token(args.claw_token))
    payload = build_niuguwang_client().get_stock_holding_list(claw_token, args.account_id)
    cache = load_inner_code_cache()
    update_cache_from_payload(cache, payload)
    save_inner_code_cache(cache)
    if parse_query_success(payload):
        ok("holdings", "持仓查询成功。", data=payload, next_action="你可以继续查委托、查成交，或者直接发起买卖。")
        return
    error("holdings", "持仓查询失败。", data=payload, next_action="可以先查账户状态，再重试。")


def cmd_deals_today(args: argparse.Namespace) -> None:
    claw_token = require_claw_token(get_claw_token(args.claw_token))
    payload = build_niuguwang_client().get_today_list(claw_token)
    if parse_query_success(payload):
        ok("deals-today", "今日成交查询成功。", data=payload)
        return
    error("deals-today", "今日成交查询失败。", data=payload)


def cmd_deals_history(args: argparse.Namespace) -> None:
    claw_token = require_claw_token(get_claw_token(args.claw_token))
    payload = build_niuguwang_client().get_history_list(claw_token)
    if parse_query_success(payload):
        ok("deals-history", "历史成交查询成功。", data=payload)
        return
    error("deals-history", "历史成交查询失败。", data=payload)


def cmd_delegates_today(args: argparse.Namespace) -> None:
    claw_token = require_claw_token(get_claw_token(args.claw_token))
    payload = build_niuguwang_client().get_today_delegate_list(claw_token)
    cache = load_inner_code_cache()
    update_cache_from_payload(cache, payload)
    save_inner_code_cache(cache)
    if parse_query_success(payload):
        ok("delegates-today", "今日委托查询成功。", data=payload)
        return
    error("delegates-today", "今日委托查询失败。", data=payload)


def cmd_delegates_history(args: argparse.Namespace) -> None:
    claw_token = require_claw_token(get_claw_token(args.claw_token))
    payload = build_niuguwang_client().get_history_delegate_list(claw_token)
    cache = load_inner_code_cache()
    update_cache_from_payload(cache, payload)
    save_inner_code_cache(cache)
    if parse_query_success(payload):
        ok("delegates-history", "历史委托查询成功。", data=payload)
        return
    error("delegates-history", "历史委托查询失败。", data=payload)


def cmd_resolve_symbol(args: argparse.Namespace) -> None:
    result = resolve_symbol(args.identifier)
    profile = result.get("profile")
    if profile:
        ok("resolve-symbol", "已识别股票。", data=result)
        return
    error("resolve-symbol", "暂时没能唯一识别这只股票。", data=result, next_action="请补充 6 位代码，或给出更完整的股票名称。")


def cmd_quote(args: argparse.Namespace) -> None:
    result = resolve_quote(args.identifier)
    if result.get("quote"):
        ok("quote", "行情查询成功。", data=result)
        return
    error("quote", "行情查询失败。", data=result, next_action="可以直接给我一个限价，我按你指定的价格下单。")


def cmd_resolve_inner_code(args: argparse.Namespace) -> None:
    result = resolve_inner_code(args.identifier, get_claw_token(args.claw_token))
    if result.get("inner_code") is not None:
        ok("resolve-inner-code", "已找到 innerCode。", data=result)
        return
    error("resolve-inner-code", "暂时没找到 innerCode。", data=result, next_action="可以先提供 innerCode，或者先查一次持仓/委托。")


def cmd_seed_inner_code(args: argparse.Namespace) -> None:
    cache = load_inner_code_cache()
    mapping = {
        "stock_code": args.stock_code.strip(),
        "stock_name": args.stock_name.strip(),
        "inner_code": int(args.inner_code),
    }
    cache[mapping["stock_code"]] = mapping
    cache[mapping["stock_name"]] = mapping
    save_inner_code_cache(cache)
    ok("seed-inner-code", "已写入 innerCode 映射。", data=mapping)


def prepare_trade_input(args: argparse.Namespace, claw_token: str) -> dict[str, Any]:
    identifier = args.identifier.strip()
    quantity = validate_quantity(int(args.quantity))
    manual_inner_code = args.inner_code
    stock_code = identifier if identifier.isdigit() and len(identifier) == 6 else ""
    stock_name = ""

    symbol_result = resolve_symbol(identifier)
    profile = symbol_result.get("profile")
    if profile:
        stock_code = profile.get("ticker", stock_code)
        stock_name = profile.get("name", stock_name)

    if manual_inner_code is not None:
        return {
            "identifier": identifier,
            "stock_code": stock_code or identifier,
            "stock_name": stock_name,
            "quantity": quantity,
            "inner_code": int(manual_inner_code),
        }

    inner_mapping = resolve_inner_code(identifier, claw_token)
    inner_code = inner_mapping.get("inner_code")
    if inner_code is None:
        raise RuntimeError("当前还缺少这只股票的 innerCode，暂时无法直接下单。")

    return {
        "identifier": identifier,
        "stock_code": inner_mapping.get("stock_code", stock_code or identifier),
        "stock_name": inner_mapping.get("stock_name", stock_name),
        "quantity": quantity,
        "inner_code": int(inner_code),
    }


def decide_trade_price(identifier: str, supplied_price: float | None, trade_type: int) -> tuple[float, str]:
    if supplied_price is not None:
        return validate_price(float(supplied_price)), "manual"

    quote_result = resolve_quote(identifier)
    quote = quote_result.get("quote")
    if not isinstance(quote, dict):
        raise RuntimeError("当前没有可用行情，请提供限价后再下单。")

    preferred_keys = ("ask1", "latest", "high") if trade_type == 1 else ("bid1", "latest", "low")
    for key in preferred_keys:
        value = quote.get(key)
        if value not in (None, "", 0, 0.0):
            return validate_price(float(value)), key

    raise RuntimeError("当前没有可用行情价格，请提供限价后再下单。")


def place_order(args: argparse.Namespace, trade_type: int) -> None:
    claw_token = require_claw_token(get_claw_token(args.claw_token))
    trade_input = prepare_trade_input(args, claw_token)
    final_price, price_source = decide_trade_price(args.identifier, args.price, trade_type)
    payload = build_niuguwang_client().add_delegate(
        claw_token,
        inner_code=int(trade_input["inner_code"]),
        amount=int(trade_input["quantity"]),
        price=float(final_price),
        trade_type=trade_type,
    )
    delegates_payload = fetch_delegates_with_retry(claw_token)
    cache = load_inner_code_cache()
    update_cache_from_payload(cache, delegates_payload)
    save_inner_code_cache(cache)

    interpreted_success = parse_order_success(payload)
    interpreted_failure = parse_order_failure(payload)
    if interpreted_success:
        summary = "委托已提交。"
        next_action = "我可以继续帮你看今日委托，确认这笔单子的状态。"
    elif interpreted_failure:
        summary = "委托提交失败。"
        next_action = "我可以继续帮你看今日委托，确认这笔单子没有挂出。"
    else:
        summary = "委托结果暂未明确。"
        next_action = "我可以继续帮你查今日委托，确认这笔单子是否真的挂出。"

    ok(
        "buy" if trade_type == 1 else "sell",
        summary,
        data=payload,
        next_action=next_action,
        trade={
            "identifier": args.identifier,
            "stockCode": trade_input["stock_code"],
            "stockName": trade_input["stock_name"],
            "innerCode": int(trade_input["inner_code"]),
            "quantity": int(trade_input["quantity"]),
            "price": float(final_price),
            "priceSource": price_source,
            "interpretedSuccess": interpreted_success,
            "interpretedFailure": interpreted_failure,
        },
        delegatesToday=delegates_payload,
    )


def cmd_buy(args: argparse.Namespace) -> None:
    place_order(args, 1)


def cmd_sell(args: argparse.Namespace) -> None:
    place_order(args, 2)


def cmd_cancel(args: argparse.Namespace) -> None:
    claw_token = require_claw_token(get_claw_token(args.claw_token))
    delegate_id = str(args.delegate_id).strip()
    if not delegate_id.isdigit():
        raise RuntimeError("delegate_id 必须是数字。")
    delegates_before = build_niuguwang_client().get_today_delegate_list(claw_token)
    existing_delegate = find_delegate_item(delegates_before, delegate_id)
    if existing_delegate is not None:
        current_state = str(existing_delegate.get("delegateState") or existing_delegate.get("state") or "").strip()
        if current_state != "0":
            error(
                "cancel",
                f"这笔委托{describe_delegate_state(current_state)}，不能再按普通撤单处理。",
                data=existing_delegate,
                next_action="如果需要，我可以继续帮你查看这笔委托或对应成交的最新状态。",
            )
            return
    payload = build_niuguwang_client().cancel_delegate(claw_token, delegate_id)
    refreshed_payload = fetch_delegates_with_retry(claw_token, attempts=4, delay_seconds=1.0)
    interpreted_success = parse_cancel_success(payload, refreshed_payload, delegate_id)
    summary = "撤单已确认成功。" if interpreted_success else "当前未确认撤单成功。"
    next_action = "我可以继续帮你看今日委托，确认这笔单子现在的状态。"
    ok(
        "cancel",
        summary,
        data=payload,
        next_action=next_action,
        interpretedSuccess=interpreted_success,
        delegatesToday=refreshed_payload,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模拟盘交易 Skill 统一 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="检查环境、状态文件和本地缓存")
    doctor.set_defaults(func=cmd_doctor)

    status = subparsers.add_parser("status", help="查看当前模拟盘状态")
    status.add_argument("--claw-token")
    status.add_argument("--save-state", action="store_true")
    status.set_defaults(func=cmd_status)

    register = subparsers.add_parser("register", help="注册模拟盘")
    register.add_argument("--real-name")
    register.add_argument("--phone", required=True)
    register.add_argument("--claw-token")
    register.add_argument("--via-backend", action="store_true")
    register.add_argument("--save-state", action="store_true")
    register.set_defaults(func=cmd_register)

    account = subparsers.add_parser("account", help="查询账户信息")
    account.add_argument("--claw-token")
    account.set_defaults(func=cmd_account)

    holdings = subparsers.add_parser("holdings", help="查询持仓")
    holdings.add_argument("--claw-token")
    holdings.add_argument("--account-id")
    holdings.set_defaults(func=cmd_holdings)

    deals_today = subparsers.add_parser("deals-today", help="查询今日成交")
    deals_today.add_argument("--claw-token")
    deals_today.set_defaults(func=cmd_deals_today)

    deals_history = subparsers.add_parser("deals-history", help="查询历史成交")
    deals_history.add_argument("--claw-token")
    deals_history.set_defaults(func=cmd_deals_history)

    delegates_today = subparsers.add_parser("delegates-today", help="查询今日委托")
    delegates_today.add_argument("--claw-token")
    delegates_today.set_defaults(func=cmd_delegates_today)

    delegates_history = subparsers.add_parser("delegates-history", help="查询历史委托")
    delegates_history.add_argument("--claw-token")
    delegates_history.set_defaults(func=cmd_delegates_history)

    resolve_symbol_parser = subparsers.add_parser("resolve-symbol", help="识别股票")
    resolve_symbol_parser.add_argument("identifier")
    resolve_symbol_parser.set_defaults(func=cmd_resolve_symbol)

    quote = subparsers.add_parser("quote", help="查询行情")
    quote.add_argument("identifier")
    quote.set_defaults(func=cmd_quote)

    resolve_inner = subparsers.add_parser("resolve-inner-code", help="解析 innerCode")
    resolve_inner.add_argument("identifier")
    resolve_inner.add_argument("--claw-token")
    resolve_inner.set_defaults(func=cmd_resolve_inner_code)

    seed = subparsers.add_parser("seed-inner-code", help="手动写入 innerCode 映射")
    seed.add_argument("--stock-code", required=True)
    seed.add_argument("--stock-name", required=True)
    seed.add_argument("--inner-code", required=True, type=int)
    seed.set_defaults(func=cmd_seed_inner_code)

    buy = subparsers.add_parser("buy", help="提交买入委托")
    buy.add_argument("identifier")
    buy.add_argument("quantity", type=int)
    buy.add_argument("--price", type=float)
    buy.add_argument("--inner-code", type=int)
    buy.add_argument("--claw-token")
    buy.set_defaults(func=cmd_buy)

    sell = subparsers.add_parser("sell", help="提交卖出委托")
    sell.add_argument("identifier")
    sell.add_argument("quantity", type=int)
    sell.add_argument("--price", type=float)
    sell.add_argument("--inner-code", type=int)
    sell.add_argument("--claw-token")
    sell.set_defaults(func=cmd_sell)

    cancel = subparsers.add_parser("cancel", help="提交撤单")
    cancel.add_argument("delegate_id")
    cancel.add_argument("--claw-token")
    cancel.set_defaults(func=cmd_cancel)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        error("runtime", str(exc), next_action="修正输入后再试一次。")
        return 1
    except Exception as exc:
        error("runtime", "命令执行失败。", data={"detail": repr(exc)}, next_action="稍后重试；如果问题持续，再检查接口状态。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
