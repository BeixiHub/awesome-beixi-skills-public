from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_TIMEOUT = 180
DEFAULT_BASE_URL = "http://82.157.41.134:9000"
DEFAULT_RETRY_ATTEMPTS = 2


def build_url(base_url: str, phase: str) -> str:
    normalized = base_url.rstrip("/")
    if phase == "phase2":
        return f"{normalized}/api/v1/phase-2/deep-diagnosis"
    if phase == "phase2_pdf":
        return f"{normalized}/api/v1/phase-2/deep-diagnosis/pdf"
    if phase == "phase3":
        return f"{normalized}/api/v1/phase-3/optimization"
    raise ValueError(f"unsupported phase: {phase}")


def _build_request(base_url: str, token: str | None, phase: str, payload: dict) -> urllib.request.Request:
    url = build_url(base_url, phase)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, data=body, headers=headers, method="POST")


def _is_retryable_url_error(exc: urllib.error.URLError) -> bool:
    reason = exc.reason
    return isinstance(reason, (TimeoutError, socket.timeout))


def _open_with_retry(request: urllib.request.Request):
    last_error: urllib.error.URLError | None = None
    for attempt in range(DEFAULT_RETRY_ATTEMPTS):
        try:
            return urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT)
        except urllib.error.URLError as exc:
            last_error = exc
            is_last_attempt = attempt == DEFAULT_RETRY_ATTEMPTS - 1
            if is_last_attempt or not _is_retryable_url_error(exc):
                break
    assert last_error is not None
    raise last_error


def call_api(base_url: str, token: str | None, phase: str, payload: dict) -> dict:
    request = _build_request(base_url, token, phase, payload)
    try:
        with _open_with_retry(request) as response:
            content = response.read().decode("utf-8")
            return json.loads(content)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"remote api http error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"remote api connection error: {exc}") from exc


def download_pdf(base_url: str, token: str | None, payload: dict) -> tuple[bytes, str | None]:
    request = _build_request(base_url, token, "phase2_pdf", payload)
    try:
        with _open_with_retry(request) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read()
            if "application/pdf" not in content_type.lower():
                detail = body.decode("utf-8", errors="replace")
                raise SystemExit(
                    f"remote api returned non-pdf content-type {content_type!r}: {detail}"
                )
            return body, response.headers.get("Content-Disposition")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"remote api http error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"remote api connection error: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call remote portfolio phase APIs from the OpenClaw client."
    )
    parser.add_argument("phase", choices=["phase2", "phase2_pdf", "phase3"])
    parser.add_argument("payload_file", help="Path to JSON payload file")
    parser.add_argument(
        "--output",
        help="Optional path to write the response. phase2/phase3 write JSON, phase2_pdf writes PDF bytes.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("PORTFOLIO_API_BASE_URL", DEFAULT_BASE_URL).strip(),
        help=(
            "Remote API base URL; defaults to PORTFOLIO_API_BASE_URL and "
            "falls back to the fixed portfolio API server when unset."
        ),
    )
    parser.add_argument(
        "--token",
        default=os.getenv("PORTFOLIO_API_TOKEN", "").strip(),
        help="Optional bearer token; defaults to PORTFOLIO_API_TOKEN",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    payload_path = Path(args.payload_file)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if args.phase == "phase2_pdf":
        pdf_bytes, content_disposition = download_pdf(args.base_url, args.token or None, payload)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(pdf_bytes)
        else:
            sys.stdout.buffer.write(pdf_bytes)
        if content_disposition:
            print(content_disposition, file=sys.stderr)
    else:
        result = call_api(args.base_url, args.token or None, args.phase, payload)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
        else:
            sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
