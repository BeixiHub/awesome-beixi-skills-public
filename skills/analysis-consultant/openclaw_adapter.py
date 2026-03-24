#!/usr/bin/env python3
"""Stable Analysis consultant adapter for the backend.

Design goals:
- Accept the OpenClaw payload shape:
  {"client":"openclaw","response_mode":"compact","license":"...","input":"..."}
- Keep UTF-8 end-to-end so Chinese output is preserved.
- Support CLI args, stdin JSON, or plain stdin text.
- Auto-organize the backend response into a clean handoff for the user.

Environment:
  MEMBER_SKILL_URL     default: http://127.0.0.1:8787/api/run
  MEMBER_SKILL_LICENSE optional fallback license token
  MEMBER_SKILL_CLIENT  default: openclaw
  MEMBER_RESPONSE_MODE default: summary
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_URL = "http://127.0.0.1:8787/api/run"


def _configure_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _read_stdin() -> str:
    if sys.stdin.isatty():
        return ""
    data = sys.stdin.read()
    return data.strip()


def _load_stdin_payload(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {"input": raw}
    except Exception:
        return {"input": raw}


def _first_nonempty(*values: Optional[str]) -> str:
    for value in values:
        if value is not None and str(value).strip() != "":
            return str(value)
    return ""


def _build_payload(args: argparse.Namespace, stdin_obj: Dict[str, Any], stdin_text: str) -> Dict[str, Any]:
    client = _first_nonempty(args.client, stdin_obj.get("client"), os.getenv("MEMBER_SKILL_CLIENT"), "openclaw")
    response_mode = _first_nonempty(args.response_mode, stdin_obj.get("response_mode"), os.getenv("MEMBER_RESPONSE_MODE"), "compact")
    license_token = _first_nonempty(args.license, stdin_obj.get("license"), os.getenv("MEMBER_SKILL_LICENSE"))
    user_input = _first_nonempty(args.input, stdin_obj.get("input"), stdin_text)

    if not license_token:
        raise SystemExit("Missing license token. Pass --license, set MEMBER_SKILL_LICENSE, or include license in stdin JSON.")
    if not user_input:
        raise SystemExit("Missing input. Pass --input, provide stdin text, or include input in stdin JSON.")

    return {
        "client": client,
        "response_mode": response_mode,
        "license": license_token,
        "input": user_input,
    }


def _post(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "body": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {"status": exc.code, "body": raw, "error": exc.reason}
    except urllib.error.URLError as exc:
        raise SystemExit(f"Backend unreachable: {exc.reason}") from exc


def _looks_like_json(text: str) -> bool:
    text = text.strip()
    return (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))


def _extract_answer_text(parsed: Any) -> str:
    if isinstance(parsed, dict):
        for key in ("answer", "output", "result", "text", "content", "message"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value
        if isinstance(parsed.get("data"), dict):
            return _extract_answer_text(parsed["data"])
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, str) and item.strip():
                return item
            if isinstance(item, dict):
                extracted = _extract_answer_text(item)
                if extracted:
                    return extracted
    if isinstance(parsed, str):
        return parsed
    return ""


def _format_handoff(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""

    if _looks_like_json(cleaned):
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return cleaned
        extracted = _extract_answer_text(parsed)
        if extracted:
            return extracted.strip()
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        return json.dumps(parsed, ensure_ascii=False, indent=2)

    return cleaned


def _render_response(result: Dict[str, Any]) -> int:
    status = int(result.get("status", 0) or 0)
    body = str(result.get("body", ""))

    if status == 403:
        print("membership check failed")
        if body:
            print(_format_handoff(body))
        return 3

    if status >= 400:
        if body:
            print(_format_handoff(body))
        else:
            print(f"HTTP {status}")
        return 1

    formatted = _format_handoff(body)
    if formatted:
        print(formatted)
    else:
        print(body)
    return 0


def main() -> int:
    _configure_stdio()

    parser = argparse.ArgumentParser(description="Stable OpenClaw adapter for the Member Skill Demo backend")
    parser.add_argument("--url", default=os.getenv("MEMBER_SKILL_URL", DEFAULT_URL), help="Backend URL")
    parser.add_argument("--client", default=None, help="Client identifier")
    parser.add_argument("--response-mode", default=None, dest="response_mode", help="Response mode")
    parser.add_argument("--license", default=None, help="License token")
    parser.add_argument("--input", default=None, help="User request")
    parser.add_argument("--raw", action="store_true", help="Print raw backend JSON instead of compact output")
    parser.add_argument("--summary", action="store_true", help="Prefer a short organized handoff summary")
    args = parser.parse_args()

    stdin_text = _read_stdin()
    stdin_obj = _load_stdin_payload(stdin_text)
    payload = _build_payload(args, stdin_obj, stdin_text if not stdin_obj else stdin_obj.get("input", ""))
    if args.raw:
        payload["response_mode"] = "raw"
    elif args.summary:
        payload["response_mode"] = "summary"

    result = _post(args.url, payload)
    return _render_response(result)


if __name__ == "__main__":
    raise SystemExit(main())
