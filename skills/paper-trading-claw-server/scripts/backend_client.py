from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

import requests

try:
    from .env_loader import load_env
except ImportError:
    from env_loader import load_env


load_env()


DEFAULT_API_BASE_URL = "http://42.193.103.122:10288/admin-api"
TRANSFER_API_PREFIX = "/paper-trading/api/v1"


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def error_payload(message: str, *, code: str = "CLI_ERROR", data: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"success": False, "code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return payload


def is_platform_result(body: Any) -> bool:
    return isinstance(body, dict) and {"code", "msg", "data"}.issubset(body.keys())


def unwrap_platform_result(body: Any) -> Any:
    """Convert transfer-service CommonResult back to the upstream skill shape."""
    if not is_platform_result(body):
        return body

    if body.get("code") == 0:
        return body.get("data")

    raw_message = str(body.get("msg") or "").strip()
    code = f"PLATFORM_{body.get('code')}"
    message = raw_message or "paper trading transfer service returned an error"
    match = re.match(r"^\[([A-Z0-9_]+)\]\s*(.*)$", raw_message)
    if match:
        code = match.group(1)
        message = match.group(2) or raw_message
    return error_payload(message, code=code, data=body.get("data"))


def effective_user_id(args: argparse.Namespace) -> str:
    return getattr(args, "user_id", None) or os.getenv("PAPER_TRADING_USER_ID", "local-user")


class BackendClient:
    def __init__(self, user_id: str | None = None) -> None:
        self.base_url = os.getenv("PAPER_TRADING_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")
        self.timeout = int(os.getenv("PAPER_TRADING_API_TIMEOUT_SECONDS", "30"))
        self.user_id = user_id or os.getenv("PAPER_TRADING_USER_ID", "local-user")
        self.token = os.getenv("PAPER_TRADING_API_TOKEN", "").strip()
        self.tenant_id = os.getenv("PAPER_TRADING_TENANT_ID", "1").strip()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "X-User-Id": self.user_id}
        if self.tenant_id:
            headers["tenant-id"] = self.tenant_id
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        url = self.base_url + path
        try:
            response = requests.request(
                method,
                url,
                json=payload,
                params={k: v for k, v in (params or {}).items() if v not in (None, "")},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"backend request failed: {exc}") from None
        try:
            body = response.json()
        except ValueError:
            body = {"success": False, "code": "NON_JSON_RESPONSE", "message": response.text}
        if response.status_code >= 400 and isinstance(body, dict):
            body.setdefault("httpStatus", response.status_code)
        return unwrap_platform_result(body)


def common_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {"userId": effective_user_id(args)}
    if getattr(args, "claw_token", None):
        payload["clawToken"] = args.claw_token
    return payload


def common_params(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {"userId": effective_user_id(args)}
    if getattr(args, "claw_token", None):
        params["clawToken"] = args.claw_token
    return params


def require_claw_token(args: argparse.Namespace, command_name: str) -> None:
    if not getattr(args, "claw_token", None):
        raise ValueError(f"{command_name} requires --claw-token. Get it from send-sms or status first.")


def client(args: argparse.Namespace) -> BackendClient:
    return BackendClient(user_id=getattr(args, "user_id", None))


def cmd_doctor(args: argparse.Namespace) -> None:
    backend = BackendClient(user_id=getattr(args, "user_id", None))
    base = backend.base_url
    data = {
        "apiBaseUrl": base,
        "tenantId": backend.tenant_id,
        "userId": backend.user_id,
        "hasApiToken": bool(os.getenv("PAPER_TRADING_API_TOKEN", "").strip()),
    }
    try:
        health = backend.request("GET", TRANSFER_API_PREFIX + "/health")
        data["health"] = health
    except Exception as exc:
        data["healthError"] = str(exc)
    print_json({"success": True, "operation": "doctor", "message": "客户端配置已返回。", "data": data})


def cmd_send_sms(args: argparse.Namespace) -> None:
    payload = common_payload(args)
    payload.update({"phone": args.phone, "realName": args.real_name})
    if args.scene:
        payload["scene"] = args.scene
    print_json(client(args).request("POST", TRANSFER_API_PREFIX + "/sms/send", payload=payload))


def cmd_verify_code(args: argparse.Namespace) -> None:
    require_claw_token(args, "verify-code")
    payload = common_payload(args)
    payload.update({"phone": args.phone, "code": args.code})
    if args.scene:
        payload["scene"] = args.scene
    print_json(client(args).request("POST", TRANSFER_API_PREFIX + "/sms/verify", payload=payload))


def cmd_register(args: argparse.Namespace) -> None:
    require_claw_token(args, "register")
    print_json(client(args).request("POST", TRANSFER_API_PREFIX + "/register", payload=common_payload(args)))


def cmd_get(args: argparse.Namespace, path: str) -> None:
    print_json(client(args).request("GET", path, params=common_params(args)))


def cmd_resolve_symbol(args: argparse.Namespace) -> None:
    payload = common_payload(args)
    payload["identifier"] = args.identifier
    print_json(client(args).request("POST", TRANSFER_API_PREFIX + "/market/resolve-symbol", payload=payload))


def cmd_quote(args: argparse.Namespace) -> None:
    payload = common_payload(args)
    payload["identifier"] = args.identifier
    print_json(client(args).request("POST", TRANSFER_API_PREFIX + "/market/quote", payload=payload))


def cmd_resolve_inner_code(args: argparse.Namespace) -> None:
    require_claw_token(args, "resolve-inner-code")
    payload = common_payload(args)
    payload["identifier"] = args.identifier
    if args.inner_code is not None:
        payload["innerCode"] = args.inner_code
    print_json(client(args).request("POST", TRANSFER_API_PREFIX + "/inner-code/resolve", payload=payload))


def cmd_seed_inner_code(args: argparse.Namespace) -> None:
    require_claw_token(args, "seed-inner-code")
    payload = common_payload(args)
    payload.update({"stockCode": args.stock_code, "stockName": args.stock_name or "", "innerCode": args.inner_code})
    print_json(client(args).request("POST", TRANSFER_API_PREFIX + "/inner-code/seed", payload=payload))


def cmd_order(args: argparse.Namespace, trade_type: str) -> None:
    require_claw_token(args, trade_type)
    payload = common_payload(args)
    payload.update({"identifier": args.identifier, "quantity": args.quantity})
    if args.price is not None:
        payload["price"] = args.price
    if args.inner_code is not None:
        payload["innerCode"] = args.inner_code
    path = TRANSFER_API_PREFIX + ("/orders/buy" if trade_type == "buy" else "/orders/sell")
    print_json(client(args).request("POST", path, payload=payload))


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user-id", help="业务用户 ID；默认取 PAPER_TRADING_USER_ID。")
    parser.add_argument("--claw-token", help="指定已有 clawToken。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper trading claw skill API client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="检查后端连接和客户端配置")
    doctor.add_argument("--user-id")
    doctor.set_defaults(func=cmd_doctor)

    send_sms = subparsers.add_parser("send-sms", help="发送手机号验证码")
    add_common_options(send_sms)
    send_sms.add_argument("--phone", required=True)
    send_sms.add_argument("--real-name", required=True)
    send_sms.add_argument("--scene")
    send_sms.set_defaults(func=cmd_send_sms)

    verify = subparsers.add_parser("verify-code", help="校验短信验证码")
    add_common_options(verify)
    verify.add_argument("--phone", required=True)
    verify.add_argument("--code", required=True)
    verify.add_argument("--scene")
    verify.set_defaults(func=cmd_verify_code)

    register = subparsers.add_parser("register", help="完成牛股王模拟盘注册绑定")
    add_common_options(register)
    register.set_defaults(func=cmd_register)

    for command, path, help_text in (
        ("status", TRANSFER_API_PREFIX + "/status", "查看绑定状态"),
        ("account", TRANSFER_API_PREFIX + "/account", "查询账户"),
        ("holdings", TRANSFER_API_PREFIX + "/holdings", "查询持仓"),
        ("deals-today", TRANSFER_API_PREFIX + "/deals/today", "查询今日成交"),
        ("deals-history", TRANSFER_API_PREFIX + "/deals/history", "查询历史成交"),
        ("delegates-today", TRANSFER_API_PREFIX + "/delegates/today", "查询今日委托"),
        ("delegates-history", TRANSFER_API_PREFIX + "/delegates/history", "查询历史委托"),
        ("usage-report", TRANSFER_API_PREFIX + "/usage/report", "查看使用统计"),
    ):
        sub = subparsers.add_parser(command, help=help_text)
        add_common_options(sub)
        sub.set_defaults(func=lambda args, route=path: cmd_get(args, route))

    resolve_symbol = subparsers.add_parser("resolve-symbol", help="识别股票代码或名称")
    add_common_options(resolve_symbol)
    resolve_symbol.add_argument("identifier")
    resolve_symbol.set_defaults(func=cmd_resolve_symbol)

    quote = subparsers.add_parser("quote", help="查询实时行情")
    add_common_options(quote)
    quote.add_argument("identifier")
    quote.set_defaults(func=cmd_quote)

    resolve_inner = subparsers.add_parser("resolve-inner-code", help="解析交易所需 innerCode")
    add_common_options(resolve_inner)
    resolve_inner.add_argument("identifier")
    resolve_inner.add_argument("--inner-code", type=int)
    resolve_inner.set_defaults(func=cmd_resolve_inner_code)

    seed = subparsers.add_parser("seed-inner-code", help="手工保存 innerCode 映射")
    add_common_options(seed)
    seed.add_argument("--stock-code", required=True)
    seed.add_argument("--stock-name")
    seed.add_argument("--inner-code", required=True, type=int)
    seed.set_defaults(func=cmd_seed_inner_code)

    buy = subparsers.add_parser("buy", help="买入委托")
    add_common_options(buy)
    buy.add_argument("identifier")
    buy.add_argument("--quantity", required=True, type=int)
    buy.add_argument("--price", type=float)
    buy.add_argument("--inner-code", type=int)
    buy.set_defaults(func=lambda args: cmd_order(args, "buy"))

    sell = subparsers.add_parser("sell", help="卖出委托")
    add_common_options(sell)
    sell.add_argument("identifier")
    sell.add_argument("--quantity", required=True, type=int)
    sell.add_argument("--price", type=float)
    sell.add_argument("--inner-code", type=int)
    sell.set_defaults(func=lambda args: cmd_order(args, "sell"))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except Exception as exc:
        print_json(error_payload(str(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
