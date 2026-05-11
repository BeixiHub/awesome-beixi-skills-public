from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from credentials_utils import load_credentials


load_credentials()


# All traffic goes through the ReportServer Java gateway (Yudao) hosted on the
# BeixiHub (蓓曦) 星途平台 at admin.deepseekdata.com. The gateway internally
# forwards to the Python portfolio_api; clients never reference that backend
# IP directly. Override with PORTFOLIO_API_BASE_URL only for local dev.
DEFAULT_BASE_URL = "https://admin.deepseekdata.com"
API_PATH_PREFIX = "/admin-api/aireport2/portfolio-health"

# Yudao is multi-tenant. Every request must carry a `tenant-id` header or the
# platform's tenant filter refuses with
#   {"code":400,"msg":"请求的租户标识未传递，请进行排查"}
# The public BeixiHub tenant is 1; override with PORTFOLIO_API_TENANT_ID only
# if you're on a dedicated tenant.
DEFAULT_TENANT_ID = "1"

# Phase 2 wall-clock = roughly (fixed overhead) + (per-stock work):
#   ~10 min fixed: LLM risk summary, QVeris bootstrapping, PDF Chromium render
#   ~1-3 min per stock: QVeris fetch + per-stock LLM sentiment
# We apply a simple linear `stock_count × 3 min` with a `30 min` floor to
# cover small portfolios whose absolute time is dominated by fixed overhead.
# Measured 2026-04-20: 2 stocks → 11min, 11 stocks → ~28min.
# 82.157.41.134 nginx has `proxy_read_timeout 7200s` (120 min), so any
# portfolio up to ~40 holdings stays below the server-side ceiling.
TIMEOUT_SECONDS_PER_STOCK = 180
MIN_TIMEOUT_SECONDS = 1800  # 30 min floor


def count_holding_codes(payload: dict) -> int:
    """Count stock codes in the payload.

    Phase 2: holdings live at payload["holdings"].
    Phase 3: holdings live at payload["diagnosis_result"]["_internal"]["holdings"].
    """
    holdings = payload.get("holdings")
    if not holdings:
        diagnosis_result = payload.get("diagnosis_result", {})
        if isinstance(diagnosis_result, dict):
            internal = diagnosis_result.get("_internal", {})
            if isinstance(internal, dict):
                holdings = internal.get("holdings", [])
    if not isinstance(holdings, list):
        holdings = []
    return len([h for h in holdings if h.get("code")])


def _compute_timeout(payload: dict) -> tuple[int, int]:
    stock_count = max(count_holding_codes(payload), 1)
    timeout = max(stock_count * TIMEOUT_SECONDS_PER_STOCK, MIN_TIMEOUT_SECONDS)
    return timeout, stock_count


def build_url(base_url: str, phase: str) -> str:
    normalized = base_url.rstrip("/")
    if phase == "phase2":
        return f"{normalized}{API_PATH_PREFIX}/phase-2/deep-diagnosis"
    if phase == "phase2_pdf":
        return f"{normalized}{API_PATH_PREFIX}/phase-2/deep-diagnosis/pdf"
    if phase == "phase3":
        return f"{normalized}{API_PATH_PREFIX}/phase-3/optimization"
    raise ValueError(f"unsupported phase: {phase}")


def _build_request(
    base_url: str,
    api_key: str,
    phase: str,
    payload: dict,
    idempotency_key: str,
    tenant_id: str,
) -> urllib.request.Request:
    url = build_url(base_url, phase)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
        "X-Idempotency-Key": idempotency_key,
        "tenant-id": tenant_id,
    }
    return urllib.request.Request(url, data=body, headers=headers, method="POST")


def _format_api_error(status_code: int, body: str) -> str:
    """Render an HTTP error body into a human-readable message.

    Three envelope shapes are recognized:
      1. Yudao/Java gateway failures:  {"code":400,"msg":"...","data":null}
      2. FastAPI-wrapped Python errors: {"detail":{"status":"error","error_message":"..."}}
      3. Raw Python envelope (if Python were called directly):
           {"status":"error","error_message":"..."}
    Anything else falls back to the truncated raw body.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        # FastAPI unpacks HTTPException(detail=...) under the "detail" key.
        inner = parsed.get("detail") if isinstance(parsed.get("detail"), dict) else parsed
        if inner.get("status") == "error":
            message = inner.get("error_message") or ""
            return f"HTTP {status_code}: {message}".rstrip(": ")
        if "msg" in parsed:
            msg = parsed.get("msg") or ""
            code = parsed.get("code")
            suffix = f" (server code={code})" if code is not None else ""
            if msg:
                return f"HTTP {status_code}: {msg}{suffix}"
            return f"HTTP {status_code}{suffix}"
    return f"HTTP {status_code}: {body[:500]}"


def _parse_json_object(text: str, context: str) -> dict:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise SystemExit(f"{context} returned invalid json: {text[:500]}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{context} returned non-object json")
    return parsed


def _read_http_error_detail(exc: urllib.error.HTTPError) -> str:
    return exc.read().decode("utf-8", errors="replace")


def _should_fallback_to_sync(exc: urllib.error.HTTPError | urllib.error.URLError) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {404, 405, 501}
    return isinstance(exc, urllib.error.URLError)


def _describe_urlerror(exc: urllib.error.URLError) -> str:
    reason = exc.reason if exc.reason is not None else exc
    return str(reason)


def _prepare_request(
    base_url: str,
    api_key: str,
    tenant_id: str,
    phase: str,
    payload: dict,
) -> tuple[urllib.request.Request, int]:
    idempotency_key = str(uuid.uuid4())
    timeout, stock_count = _compute_timeout(payload)
    raw_timeout = stock_count * TIMEOUT_SECONDS_PER_STOCK
    sys.stderr.write(
        f"[timeout] {stock_count} stocks -> "
        f"max({raw_timeout}s, {MIN_TIMEOUT_SECONDS}s floor) = {timeout}s\n"
    )
    request = _build_request(base_url, api_key, phase, payload, idempotency_key, tenant_id)
    return request, timeout


def _send_with_retry(request: urllib.request.Request, read_fn, timeout: int):
    """POST the request; on a connection-level failure, retry once.

    The same X-Idempotency-Key header is reused on the retry so the Java
    gateway deduplicates via `openapi_consume_record` and never double-charges.
    HTTPError (4xx/5xx) is a real server response and is NOT retried — those
    are either deterministic errors or Python-backend-level failures (which
    the gateway does not charge for either way).
    """
    first_error: urllib.error.URLError | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return read_fn(response)
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        first_error = exc
        sys.stderr.write(
            f"[retry] transient network error ({_describe_urlerror(exc)}); "
            f"retrying once with same idempotency key\n"
        )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return read_fn(response)
    except urllib.error.URLError as exc:
        first_reason = _describe_urlerror(first_error) if first_error else "unknown"
        second_reason = _describe_urlerror(exc)
        raise urllib.error.URLError(
            f"{second_reason} (first attempt: {first_reason})"
        ) from exc


def _read_json(response) -> dict:
    content = response.read().decode("utf-8")
    return _parse_json_object(content, "remote api")


def _read_pdf(response) -> tuple[bytes, str | None]:
    content_type = response.headers.get("Content-Type", "")
    body = response.read()
    if "application/pdf" not in content_type.lower():
        detail = body.decode("utf-8", errors="replace")[:500]
        raise SystemExit(
            f"remote api returned non-pdf content-type {content_type!r}: {detail}"
        )
    content_disposition = response.headers.get("Content-Disposition")
    return body, content_disposition


# ---------------------------------------------------------------------------
# Async task API (submit + poll)
# ---------------------------------------------------------------------------
# The server (app.py ≥0.5.0) exposes POST /tasks and GET /tasks/{id}.
# If the server doesn't support tasks yet, we fall back to the legacy sync
# call transparently — the caller sees no difference.

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_FACTOR = 1.2
MAX_CONSECUTIVE_POLL_ERRORS = 5


def _submit_task(
    base_url: str,
    api_key: str,
    tenant_id: str,
    phase: str,
    payload: dict,
) -> str:
    url = base_url.rstrip("/") + f"{API_PATH_PREFIX}/phc-tasks"
    body = json.dumps({"phase": phase, **payload}, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
        "tenant-id": tenant_id,
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = _parse_json_object(
            resp.read().decode("utf-8"),
            "remote task submit",
        )
    task_id = data.get("task_id", "")
    if not task_id:
        raise SystemExit("remote task submit returned empty task_id")
    sys.stderr.write(
        f"[task] submitted {task_id[:8]}... "
        f"(queue={data.get('queue_depth', '?')}, "
        f"running={data.get('running', '?')})\n"
    )
    return task_id


def _poll_task(
    base_url: str,
    api_key: str,
    tenant_id: str,
    task_id: str,
    timeout: int,
) -> dict:
    url = base_url.rstrip("/") + f"{API_PATH_PREFIX}/phc-tasks/{task_id}"
    headers = {
        "X-API-Key": api_key,
        "tenant-id": tenant_id,
    }
    deadline = time.time() + timeout
    start = time.time()
    consecutive_network_errors = 0

    while time.time() < deadline:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = _parse_json_object(
                    resp.read().decode("utf-8"),
                    "remote task poll",
                )
            consecutive_network_errors = 0
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SystemExit(
                    f"task {task_id[:8]}... not found on server "
                    f"(expired or invalid)"
                ) from exc
            raise
        except urllib.error.URLError as exc:
            consecutive_network_errors += 1
            sys.stderr.write(
                f"[task] poll network error ({_describe_urlerror(exc)}), "
                f"retrying {consecutive_network_errors}/{MAX_CONSECUTIVE_POLL_ERRORS}\n"
            )
            if consecutive_network_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                raise urllib.error.URLError(
                    f"{_describe_urlerror(exc)} "
                    f"(reached {MAX_CONSECUTIVE_POLL_ERRORS} consecutive poll failures)"
                ) from exc
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        status = data.get("status", "")

        if status == "completed":
            elapsed = int(time.time() - start)
            sys.stderr.write(
                f"[task] {task_id[:8]}... completed ({elapsed}s)\n"
            )
            return data.get("result", {})

        if status == "failed":
            err = data.get("error_message", "unknown error")
            raise SystemExit(f"remote task failed: {err}")

        elapsed = int(time.time() - start)
        sys.stderr.write(
            f"[task] {task_id[:8]}... {status} ({elapsed}s elapsed)\n"
        )
        time.sleep(POLL_INTERVAL_SECONDS)

    raise SystemExit(f"task {task_id[:8]}... timed out after {timeout}s")


def _call_api_sync(
    base_url: str, api_key: str, tenant_id: str, phase: str, payload: dict,
) -> dict:
    request, timeout = _prepare_request(base_url, api_key, tenant_id, phase, payload)
    try:
        return _send_with_retry(request, _read_json, timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"remote api error: {_format_api_error(exc.code, detail)}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"remote api connection error: {_describe_urlerror(exc)}") from exc


def _raise_task_request_error(
    stage: str,
    exc: urllib.error.HTTPError | urllib.error.URLError,
) -> None:
    if isinstance(exc, urllib.error.HTTPError):
        detail = _read_http_error_detail(exc)
        raise SystemExit(
            f"remote task {stage} error: {_format_api_error(exc.code, detail)}"
        ) from exc
    raise SystemExit(
        f"remote task {stage} connection error: {_describe_urlerror(exc)}"
    ) from exc


def _run_async_task_with_fallback(
    base_url: str,
    api_key: str,
    tenant_id: str,
    phase: str,
    payload: dict,
    sync_fn,
    result_fn,
):
    timeout, _stock_count = _compute_timeout(payload)

    try:
        task_id = _submit_task(base_url, api_key, tenant_id, phase, payload)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        if not _should_fallback_to_sync(exc):
            _raise_task_request_error("submit", exc)
        sys.stderr.write(
            f"[task] async submit failed ({exc}), falling back to sync\n"
        )
        return sync_fn(base_url, api_key, tenant_id, payload)

    poll_timeout = int(timeout * POLL_TIMEOUT_FACTOR)
    try:
        result = _poll_task(base_url, api_key, tenant_id, task_id, poll_timeout)
    except urllib.error.HTTPError as exc:
        _raise_task_request_error("poll", exc)
    except urllib.error.URLError as exc:
        _raise_task_request_error("poll", exc)
    return result_fn(result)


def _return_json_result(result: dict) -> dict:
    return result


def call_api(base_url: str, api_key: str, tenant_id: str, phase: str, payload: dict) -> dict:
    return _run_async_task_with_fallback(
        base_url,
        api_key,
        tenant_id,
        phase,
        payload,
        sync_fn=lambda base_url, api_key, tenant_id, payload: _call_api_sync(
            base_url,
            api_key,
            tenant_id,
            phase,
            payload,
        ),
        result_fn=_return_json_result,
    )


def _download_pdf_sync(
    base_url: str, api_key: str, tenant_id: str, payload: dict,
) -> tuple[bytes, str | None]:
    request, timeout = _prepare_request(
        base_url, api_key, tenant_id, "phase2_pdf", payload,
    )
    try:
        return _send_with_retry(request, _read_pdf, timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"remote api error: {_format_api_error(exc.code, detail)}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"remote api connection error: {_describe_urlerror(exc)}") from exc


def _decode_pdf_task_result(result: dict) -> tuple[bytes, str | None]:
    pdf_b64 = result.get("pdf_base64", "")
    if not pdf_b64:
        raise SystemExit("task completed but no pdf_base64 in result")
    try:
        pdf_bytes = base64.b64decode(pdf_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SystemExit("task completed but returned invalid pdf_base64") from exc
    if not pdf_bytes.startswith(b"%PDF"):
        raise SystemExit("task completed but returned non-pdf bytes")
    return pdf_bytes, 'attachment; filename="diagnosis_report.pdf"'


def download_pdf(
    base_url: str, api_key: str, tenant_id: str, payload: dict,
) -> tuple[bytes, str | None]:
    return _run_async_task_with_fallback(
        base_url,
        api_key,
        tenant_id,
        "phase2_pdf",
        payload,
        sync_fn=_download_pdf_sync,
        result_fn=_decode_pdf_task_result,
    )


def _extract_client_markdown(result: dict, json_path: Path) -> Path | None:
    """Extract client_output.markdown and save as .md alongside the JSON."""
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    co = data.get("client_output") if isinstance(data, dict) else None
    if not isinstance(co, dict):
        return None
    markdown = co.get("markdown", "")
    if not markdown:
        return None
    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown + "\n", encoding="utf-8")
    return md_path


def _build_result_summary(result: dict, phase: str, output_file: str) -> dict:
    """Extract key fields from a phase result for stdout confirmation."""
    summary: dict = {
        "status": result.get("status", "ok"),
        "phase": phase,
        "output_file": output_file,
    }
    if phase in ("phase2", "phase2_pdf"):
        co = result.get("client_output") or result.get("data", {}).get("client_output")
        if isinstance(co, dict):
            summary["headline"] = co.get("headline", "")
            section_count = len(co.get("sections", []))
            table_count = len(co.get("tables", []))
            summary["sections"] = section_count
            summary["tables"] = table_count
            summary["has_markdown"] = bool(co.get("markdown"))
    elif phase == "phase3":
        data = result.get("data", {})
        co = data.get("client_output") if isinstance(data, dict) else None
        if isinstance(co, dict):
            summary["headline"] = co.get("headline", "")
            summary["has_markdown"] = bool(co.get("markdown"))
        ei = data.get("execution_info") if isinstance(data, dict) else None
        if isinstance(ei, dict):
            summary["rescore_executed"] = ei.get("rescore_executed")
            summary["stress_test_executed"] = ei.get("stress_test_executed")
            warnings = ei.get("warnings", [])
            if warnings:
                summary["warnings"] = warnings
    em = result.get("error_message")
    if em:
        summary["error_message"] = em
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call remote portfolio phase APIs via the ReportServer gateway."
    )
    parser.add_argument("phase", choices=["phase2", "phase2_pdf", "phase3"])
    parser.add_argument("payload_file", help="Path to JSON payload file")
    parser.add_argument(
        "--output",
        help="Optional output path. phase2/phase3 write JSON, phase2_pdf writes PDF bytes.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("PORTFOLIO_API_BASE_URL", DEFAULT_BASE_URL).strip(),
        help=f"Gateway base URL (default: {DEFAULT_BASE_URL}). Override with PORTFOLIO_API_BASE_URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("PORTFOLIO_API_KEY", "").strip(),
        help="OpenAPI user key sent as X-API-Key; defaults to PORTFOLIO_API_KEY. Required.",
    )
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("PORTFOLIO_API_TENANT_ID", DEFAULT_TENANT_ID).strip(),
        help=f"Yudao tenant id (default: {DEFAULT_TENANT_ID}). Override with PORTFOLIO_API_TENANT_ID.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.base_url:
        sys.stderr.write("error: --base-url is required (or PORTFOLIO_API_BASE_URL)\n")
        return 2
    if not args.api_key:
        sys.stderr.write(
            "error: PORTFOLIO_API_KEY (or --api-key) is required. "
            "Apply for one at https://deepseekdata.com/arena.html (蓓曦星途平台).\n"
        )
        return 2
    if not args.tenant_id:
        sys.stderr.write("error: --tenant-id is required (or PORTFOLIO_API_TENANT_ID)\n")
        return 2

    payload_path = Path(args.payload_file)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if args.phase == "phase2_pdf":
        pdf_bytes, content_disposition = download_pdf(
            args.base_url, args.api_key, args.tenant_id, payload
        )
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(pdf_bytes)
            sys.stdout.write(json.dumps({
                "status": "ok",
                "phase": args.phase,
                "output_file": str(output_path),
                "size_bytes": len(pdf_bytes),
            }, ensure_ascii=False) + "\n")
        else:
            sys.stdout.buffer.write(pdf_bytes)
        if content_disposition:
            print(content_disposition, file=sys.stderr)
    else:
        result = call_api(
            args.base_url, args.api_key, args.tenant_id, args.phase, payload
        )
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
            summary = _build_result_summary(result, args.phase, str(output_path))
            if args.phase == "phase3":
                md_path = _extract_client_markdown(result, output_path)
                if md_path:
                    summary["markdown_file"] = str(md_path)
            sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
