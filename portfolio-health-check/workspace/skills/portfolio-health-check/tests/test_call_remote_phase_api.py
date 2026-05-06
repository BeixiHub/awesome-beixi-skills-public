from __future__ import annotations

import importlib.util
import json
import socket
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "call_remote_phase_api.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("call_remote_phase_api", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


GATEWAY_PREFIX = "/admin-api/aireport2/portfolio-health"
TENANT = "1"
API_KEY = "sk-test-key"


class FakeResponse:
    def __init__(self, body: bytes, content_type: str, disposition: str | None = None) -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}
        if disposition is not None:
            self.headers["Content-Disposition"] = disposition

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class BuildUrlTests(unittest.TestCase):
    def test_phase2_route(self) -> None:
        self.assertEqual(
            MODULE.build_url("https://java.example.com/", "phase2"),
            f"https://java.example.com{GATEWAY_PREFIX}/phase-2/deep-diagnosis",
        )

    def test_phase2_pdf_route(self) -> None:
        self.assertEqual(
            MODULE.build_url("https://java.example.com", "phase2_pdf"),
            f"https://java.example.com{GATEWAY_PREFIX}/phase-2/deep-diagnosis/pdf",
        )

    def test_phase3_route(self) -> None:
        self.assertEqual(
            MODULE.build_url("https://java.example.com", "phase3"),
            f"https://java.example.com{GATEWAY_PREFIX}/phase-3/optimization",
        )

    def test_unsupported_phase_raises(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.build_url("https://java.example.com", "phase4")


class CountHoldingCodesTests(unittest.TestCase):
    def test_counts_top_level_holdings(self) -> None:
        payload = {"holdings": [{"code": "600519.SH"}, {"code": "300750.SZ"}, {"code": "000001.SZ"}]}
        self.assertEqual(MODULE.count_holding_codes(payload), 3)

    def test_counts_phase3_nested_holdings(self) -> None:
        payload = {
            "diagnosis_result": {
                "_internal": {"holdings": [{"code": "600519.SH"}, {"code": "300750.SZ"}]}
            },
            "constraints": {},
        }
        self.assertEqual(MODULE.count_holding_codes(payload), 2)

    def test_ignores_entries_without_code(self) -> None:
        payload = {"holdings": [{"code": "600519.SH"}, {"name": "no code"}, {"code": ""}]}
        self.assertEqual(MODULE.count_holding_codes(payload), 1)

    def test_returns_zero_on_empty_payload(self) -> None:
        self.assertEqual(MODULE.count_holding_codes({}), 0)

    def test_returns_zero_when_holdings_missing_from_both_paths(self) -> None:
        payload = {"something_else": {"foo": "bar"}}
        self.assertEqual(MODULE.count_holding_codes(payload), 0)


class ComputeTimeoutTests(unittest.TestCase):
    def test_timeout_is_floor_for_small_portfolios(self) -> None:
        # 3 stocks × 180 = 540, but floor is 1800
        payload = {"holdings": [{"code": "A"}, {"code": "B"}, {"code": "C"}]}
        timeout, stock_count = MODULE._compute_timeout(payload)
        self.assertEqual(timeout, 1800)
        self.assertEqual(stock_count, 3)

    def test_timeout_scales_above_floor_for_larger_portfolios(self) -> None:
        # 15 stocks × 180 = 2700 > floor 1800
        payload = {"holdings": [{"code": f"C{i}"} for i in range(15)]}
        timeout, stock_count = MODULE._compute_timeout(payload)
        self.assertEqual(timeout, 15 * 180)
        self.assertEqual(stock_count, 15)

    def test_timeout_for_single_stock(self) -> None:
        # 1 stock × 180 = 180, but floor is 1800
        payload = {"holdings": [{"code": "A"}]}
        timeout, stock_count = MODULE._compute_timeout(payload)
        self.assertEqual(timeout, 1800)
        self.assertEqual(stock_count, 1)

    def test_timeout_floors_on_empty_payload(self) -> None:
        # empty payload → treated as 1 stock (×180=180), then floor raises to 1800
        timeout, stock_count = MODULE._compute_timeout({})
        self.assertEqual(timeout, 1800)
        self.assertEqual(stock_count, 1)

    def test_timeout_boundary_at_10_stocks(self) -> None:
        # 10 stocks × 180 = 1800 = floor; either branch acceptable
        payload = {"holdings": [{"code": f"C{i}"} for i in range(10)]}
        timeout, stock_count = MODULE._compute_timeout(payload)
        self.assertEqual(timeout, 1800)
        self.assertEqual(stock_count, 10)


class CallApiTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.submit_task_patcher = mock.patch.object(
            MODULE,
            "_submit_task",
            side_effect=urllib.error.URLError("task api unavailable"),
        )
        self.submit_task_patcher.start()
        self.addCleanup(self.submit_task_patcher.stop)

    def test_sends_x_api_key_header(self) -> None:
        response = FakeResponse(
            body=json.dumps({"status": "ok"}).encode("utf-8"),
            content_type="application/json",
        )
        captured = []

        def capture(req, timeout=None):
            captured.append(req)
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.call_api("https://java.example.com", "sk-testkey-123", TENANT, "phase2", {"x": 1})

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].get_header("X-api-key"), "sk-testkey-123")

    def test_sends_tenant_id_header(self) -> None:
        response = FakeResponse(
            body=json.dumps({"status": "ok"}).encode("utf-8"),
            content_type="application/json",
        )
        captured = []

        def capture(req, timeout=None):
            captured.append(req)
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.call_api("https://java.example.com", API_KEY, "42", "phase2", {"x": 1})

        self.assertEqual(captured[0].get_header("Tenant-id"), "42")

    def test_sends_idempotency_key_header_as_uuid(self) -> None:
        response = FakeResponse(
            body=json.dumps({"status": "ok"}).encode("utf-8"),
            content_type="application/json",
        )
        captured = []

        def capture(req, timeout=None):
            captured.append(req)
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})

        key = captured[0].get_header("X-idempotency-key")
        self.assertIsNotNone(key)
        self.assertEqual(len(key), 36, f"Expected UUID length 36, got {key!r}")

    def test_does_not_send_authorization_header(self) -> None:
        response = FakeResponse(
            body=json.dumps({"status": "ok"}).encode("utf-8"),
            content_type="application/json",
        )
        captured = []

        def capture(req, timeout=None):
            captured.append(req)
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})

        self.assertIsNone(captured[0].get_header("Authorization"))

    def test_timeout_passed_to_urlopen_honors_floor(self) -> None:
        # 4 stocks would be 4×180=720 without the floor; with floor → 1800
        response = FakeResponse(
            body=json.dumps({"status": "ok"}).encode("utf-8"),
            content_type="application/json",
        )
        captured_timeouts = []

        def capture(req, timeout=None):
            captured_timeouts.append(timeout)
            return response

        payload = {"holdings": [{"code": "A"}, {"code": "B"}, {"code": "C"}, {"code": "D"}]}
        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", payload)

        self.assertEqual(captured_timeouts, [1800])

    def test_timeout_passed_to_urlopen_scales_above_floor(self) -> None:
        # 20 stocks × 180 = 3600 > floor
        response = FakeResponse(
            body=json.dumps({"status": "ok"}).encode("utf-8"),
            content_type="application/json",
        )
        captured_timeouts = []

        def capture(req, timeout=None):
            captured_timeouts.append(timeout)
            return response

        payload = {"holdings": [{"code": f"C{i}"} for i in range(20)]}
        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", payload)

        self.assertEqual(captured_timeouts, [20 * 180])

    def test_returns_parsed_json_on_success(self) -> None:
        response = FakeResponse(
            body=json.dumps({"status": "ok", "data": {"foo": "bar"}}).encode("utf-8"),
            content_type="application/json",
        )
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            result = MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["foo"], "bar")

    def test_retries_once_on_timeout_with_same_idempotency_key(self) -> None:
        response = FakeResponse(
            body=json.dumps({"status": "ok"}).encode("utf-8"),
            content_type="application/json",
        )
        captured_keys = []

        def capture(req, timeout=None):
            captured_keys.append(req.get_header("X-idempotency-key"))
            if len(captured_keys) == 1:
                raise urllib.error.URLError(socket.timeout("timed out"))
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            result = MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(captured_keys), 2)
        self.assertEqual(captured_keys[0], captured_keys[1])

    def test_both_attempts_failing_propagates_as_systemexit(self) -> None:
        side_effect = [
            urllib.error.URLError(socket.timeout("timed out")),
            urllib.error.URLError("gateway unreachable"),
        ]
        with mock.patch.object(
            MODULE.urllib.request, "urlopen", side_effect=side_effect
        ) as mocked:
            with self.assertRaises(SystemExit) as raised:
                MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})
        self.assertEqual(mocked.call_count, 2)
        self.assertIn("connection error", str(raised.exception))
        self.assertIn("gateway unreachable", str(raised.exception))
        self.assertIn("first attempt: timed out", str(raised.exception))

    def test_http_error_is_not_retried(self) -> None:
        http_error = urllib.error.HTTPError(
            url="https://java.example.com/x",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        http_error.read = lambda: b'{"code":400,"msg":"bad payload"}'
        with mock.patch.object(
            MODULE.urllib.request, "urlopen", side_effect=http_error
        ) as mocked:
            with self.assertRaises(SystemExit):
                MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})
        self.assertEqual(mocked.call_count, 1)

    def test_http_error_with_java_envelope_is_formatted(self) -> None:
        java_error = urllib.error.HTTPError(
            url="https://java.example.com/x",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        java_error.read = lambda: b'{"code":400,"msg":"\xe5\xbc\x80\xe6\x94\xbe\xe5\xb9\xb3\xe5\x8f\xb0\xe7\xa7\xaf\xe5\x88\x86\xe4\xb8\x8d\xe8\xb6\xb3","data":null}'
        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=java_error):
            with self.assertRaises(SystemExit) as raised:
                MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})
        self.assertIn("开放平台积分不足", str(raised.exception))
        self.assertIn("server code=400", str(raised.exception))

    def test_http_error_with_python_envelope_is_formatted(self) -> None:
        # Raw Python envelope (no FastAPI detail wrapping) — tests backwards
        # compat for direct Python calls if we ever reintroduce them.
        py_error = urllib.error.HTTPError(
            url="https://java.example.com/x",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        py_error.read = lambda: b'{"status":"error","error_message":"Payload must include params","data":null}'
        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=py_error):
            with self.assertRaises(SystemExit) as raised:
                MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})
        self.assertIn("Payload must include params", str(raised.exception))

    def test_http_error_with_fastapi_wrapped_envelope_is_formatted(self) -> None:
        # FastAPI wraps HTTPException(detail=...) as {"detail": {...}}; Java
        # gateway passes it through byte-for-byte, so this is what we see in
        # practice when Python's payload validator refuses a request.
        fastapi_error = urllib.error.HTTPError(
            url="https://java.example.com/x",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        fastapi_error.read = lambda: (
            b'{"detail":{"status":"error",'
            b'"error_message":"Payload must include params",'
            b'"data":null,"artifacts":null}}'
        )
        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=fastapi_error):
            with self.assertRaises(SystemExit) as raised:
                MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})
        self.assertIn("Payload must include params", str(raised.exception))


class AsyncCallApiTests(unittest.TestCase):
    def test_uses_task_submit_and_poll_when_available(self) -> None:
        with (
            mock.patch.object(MODULE, "_submit_task", return_value="task-123") as submit,
            mock.patch.object(
                MODULE,
                "_poll_task",
                return_value={"status": "ok", "data": {"foo": "bar"}},
            ) as poll,
        ):
            result = MODULE.call_api(
                "https://java.example.com",
                API_KEY,
                TENANT,
                "phase2",
                {"holdings": [{"code": "600519.SH"}]},
            )

        self.assertEqual(result["status"], "ok")
        submit.assert_called_once()
        poll.assert_called_once_with(
            "https://java.example.com",
            API_KEY,
            TENANT,
            "task-123",
            2160,
        )

    def test_submit_404_falls_back_to_sync_api(self) -> None:
        response = FakeResponse(
            body=json.dumps({"status": "ok", "data": {"source": "sync"}}).encode("utf-8"),
            content_type="application/json",
        )
        task_missing = urllib.error.HTTPError(
            url="https://java.example.com/tasks",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        task_missing.read = lambda: b'{"code":404,"msg":"not found"}'

        with (
            mock.patch.object(MODULE, "_submit_task", side_effect=task_missing),
            mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response) as urlopen,
        ):
            result = MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})

        self.assertEqual(result["data"]["source"], "sync")
        self.assertEqual(urlopen.call_count, 1)

    def test_submit_400_does_not_fallback_to_sync_api(self) -> None:
        bad_request = urllib.error.HTTPError(
            url="https://java.example.com/tasks",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        bad_request.read = lambda: b'{"code":400,"msg":"bad task payload"}'

        with (
            mock.patch.object(MODULE, "_submit_task", side_effect=bad_request),
            mock.patch.object(MODULE.urllib.request, "urlopen") as urlopen,
        ):
            with self.assertRaises(SystemExit) as raised:
                MODULE.call_api("https://java.example.com", API_KEY, TENANT, "phase2", {"x": 1})

        self.assertIn("remote task submit error", str(raised.exception))
        self.assertIn("bad task payload", str(raised.exception))
        urlopen.assert_not_called()

    def test_poll_http_error_is_formatted(self) -> None:
        task_error = urllib.error.HTTPError(
            url="https://java.example.com/tasks/task-123",
            code=502,
            msg="Bad Gateway",
            hdrs=None,
            fp=None,
        )
        task_error.read = lambda: b'{"code":502,"msg":"task worker unavailable"}'

        with (
            mock.patch.object(MODULE, "_submit_task", return_value="task-123"),
            mock.patch.object(MODULE, "_poll_task", side_effect=task_error),
        ):
            with self.assertRaises(SystemExit) as raised:
                MODULE.call_api(
                    "https://java.example.com",
                    API_KEY,
                    TENANT,
                    "phase2",
                    {"holdings": [{"code": "600519.SH"}]},
                )

        self.assertIn("remote task poll error", str(raised.exception))
        self.assertIn("task worker unavailable", str(raised.exception))

    def test_poll_repeated_urlerror_fails_before_global_timeout(self) -> None:
        with (
            mock.patch.object(MODULE, "_submit_task", return_value="task-123"),
            mock.patch.object(
                MODULE.urllib.request,
                "urlopen",
                side_effect=urllib.error.URLError("gateway timeout"),
            ) as urlopen,
            mock.patch.object(MODULE.time, "time", side_effect=[0, 0, 0, 1, 2, 3, 4]),
            mock.patch.object(MODULE.time, "sleep"),
        ):
            with self.assertRaises(SystemExit) as raised:
                MODULE.call_api(
                    "https://java.example.com",
                    API_KEY,
                    TENANT,
                    "phase2",
                    {"holdings": [{"code": "600519.SH"}]},
                )

        self.assertIn("remote task poll connection error", str(raised.exception))
        self.assertIn("5 consecutive poll failures", str(raised.exception))
        self.assertEqual(urlopen.call_count, 5)


class DownloadPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.submit_task_patcher = mock.patch.object(
            MODULE,
            "_submit_task",
            side_effect=urllib.error.URLError("task api unavailable"),
        )
        self.submit_task_patcher.start()
        self.addCleanup(self.submit_task_patcher.stop)

    def test_returns_bytes_and_disposition(self) -> None:
        response = FakeResponse(
            body=b"%PDF-1.4 test payload",
            content_type="application/pdf",
            disposition='attachment; filename="report.pdf"',
        )
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            body, disposition = MODULE.download_pdf(
                "https://java.example.com", API_KEY, TENANT, {"x": 1}
            )
        self.assertEqual(body, b"%PDF-1.4 test payload")
        self.assertEqual(disposition, 'attachment; filename="report.pdf"')

    def test_rejects_non_pdf_content_type(self) -> None:
        response = FakeResponse(
            body=b'{"code":0,"data":"JVBERi0xLjQ="}',
            content_type="application/json",
        )
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(SystemExit) as raised:
                MODULE.download_pdf("https://java.example.com", API_KEY, TENANT, {"x": 1})
        self.assertIn("non-pdf", str(raised.exception))

    def test_truncates_non_pdf_error_detail(self) -> None:
        response = FakeResponse(body=b"x" * 10_000, content_type="text/plain")
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(SystemExit) as raised:
                MODULE.download_pdf("https://java.example.com", API_KEY, TENANT, {"x": 1})
        self.assertLess(len(str(raised.exception)), 700)

    def test_sends_x_api_key_header(self) -> None:
        response = FakeResponse(body=b"%PDF-1.4", content_type="application/pdf")
        captured = []

        def capture(req, timeout=None):
            captured.append(req)
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.download_pdf("https://java.example.com", "sk-pdf-key", TENANT, {"x": 1})
        self.assertEqual(captured[0].get_header("X-api-key"), "sk-pdf-key")

    def test_sends_tenant_id_header(self) -> None:
        response = FakeResponse(body=b"%PDF-1.4", content_type="application/pdf")
        captured = []

        def capture(req, timeout=None):
            captured.append(req)
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.download_pdf("https://java.example.com", API_KEY, "99", {"x": 1})
        self.assertEqual(captured[0].get_header("Tenant-id"), "99")

    def test_sends_idempotency_key_header(self) -> None:
        response = FakeResponse(body=b"%PDF-1.4", content_type="application/pdf")
        captured = []

        def capture(req, timeout=None):
            captured.append(req)
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            MODULE.download_pdf("https://java.example.com", API_KEY, TENANT, {"x": 1})
        key = captured[0].get_header("X-idempotency-key")
        self.assertIsNotNone(key)
        self.assertEqual(len(key), 36)

    def test_retries_once_on_timeout(self) -> None:
        response = FakeResponse(body=b"%PDF-1.4", content_type="application/pdf")
        call_count = [0]

        def capture(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.URLError(socket.timeout("timed out"))
            return response

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=capture):
            body, _ = MODULE.download_pdf("https://java.example.com", API_KEY, TENANT, {"x": 1})

        self.assertEqual(body, b"%PDF-1.4")
        self.assertEqual(call_count[0], 2)


class AsyncDownloadPdfTests(unittest.TestCase):
    def test_decodes_pdf_from_async_task_result(self) -> None:
        with (
            mock.patch.object(MODULE, "_submit_task", return_value="task-pdf-1") as submit,
            mock.patch.object(
                MODULE,
                "_poll_task",
                return_value={"pdf_base64": "JVBERi0xLjQ="},
            ) as poll,
        ):
            body, disposition = MODULE.download_pdf(
                "https://java.example.com",
                API_KEY,
                TENANT,
                {"holdings": [{"code": "600519.SH"}]},
            )

        self.assertEqual(body, b"%PDF-1.4")
        self.assertEqual(disposition, 'attachment; filename="diagnosis_report.pdf"')
        submit.assert_called_once()
        poll.assert_called_once_with(
            "https://java.example.com",
            API_KEY,
            TENANT,
            "task-pdf-1",
            2160,
        )

    def test_submit_404_falls_back_to_sync_pdf(self) -> None:
        response = FakeResponse(body=b"%PDF-1.4", content_type="application/pdf")
        task_missing = urllib.error.HTTPError(
            url="https://java.example.com/tasks",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        task_missing.read = lambda: b'{"code":404,"msg":"not found"}'

        with (
            mock.patch.object(MODULE, "_submit_task", side_effect=task_missing),
            mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response) as urlopen,
        ):
            body, disposition = MODULE.download_pdf(
                "https://java.example.com",
                API_KEY,
                TENANT,
                {"x": 1},
            )

        self.assertEqual(body, b"%PDF-1.4")
        self.assertIsNone(disposition)
        self.assertEqual(urlopen.call_count, 1)

    def test_rejects_invalid_pdf_base64_from_async_task(self) -> None:
        with (
            mock.patch.object(MODULE, "_submit_task", return_value="task-pdf-1"),
            mock.patch.object(
                MODULE,
                "_poll_task",
                return_value={"pdf_base64": "not-base64!!"},
            ),
        ):
            with self.assertRaises(SystemExit) as raised:
                MODULE.download_pdf(
                    "https://java.example.com",
                    API_KEY,
                    TENANT,
                    {"holdings": [{"code": "600519.SH"}]},
                )

        self.assertIn("invalid pdf_base64", str(raised.exception))


class FormatApiErrorTests(unittest.TestCase):
    def test_parses_java_envelope(self) -> None:
        body = '{"code":400,"msg":"开放平台积分不足","data":null}'
        result = MODULE._format_api_error(400, body)
        self.assertIn("开放平台积分不足", result)
        self.assertIn("server code=400", result)

    def test_parses_python_envelope(self) -> None:
        body = '{"status":"error","error_message":"Missing holdings","data":null}'
        result = MODULE._format_api_error(400, body)
        self.assertIn("Missing holdings", result)
        self.assertIn("HTTP 400", result)

    def test_parses_fastapi_wrapped_envelope(self) -> None:
        # Real response shape seen in production — FastAPI packs
        # HTTPException(detail=...) under the "detail" key.
        body = (
            '{"detail":{"status":"error","error_message":"Payload must include params",'
            '"data":null,"artifacts":null}}'
        )
        result = MODULE._format_api_error(400, body)
        self.assertIn("Payload must include params", result)
        self.assertIn("HTTP 400", result)

    def test_prefers_python_error_message_when_top_level_msg_is_present(self) -> None:
        body = (
            '{"msg":"outer gateway message","detail":{"status":"error",'
            '"error_message":"Payload must include params"}}'
        )
        result = MODULE._format_api_error(400, body)
        self.assertIn("Payload must include params", result)
        self.assertNotIn("outer gateway message", result)

    def test_omits_double_space_when_java_msg_is_empty(self) -> None:
        body = '{"code":400,"msg":"","data":null}'
        result = MODULE._format_api_error(400, body)
        self.assertEqual(result, "HTTP 400 (server code=400)")

    def test_falls_back_on_non_json_body(self) -> None:
        result = MODULE._format_api_error(502, "Bad Gateway")
        self.assertIn("Bad Gateway", result)
        self.assertIn("HTTP 502", result)

    def test_truncates_very_long_non_json_body(self) -> None:
        long_body = "x" * 10_000
        result = MODULE._format_api_error(500, long_body)
        self.assertLessEqual(len(result), 600)


if __name__ == "__main__":
    unittest.main()
