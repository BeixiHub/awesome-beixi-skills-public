from __future__ import annotations

import importlib.util
import json
import socket
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "call_remote_phase_api.py"
SPEC = importlib.util.spec_from_file_location("call_remote_phase_api", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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


class CallRemotePhaseApiTests(unittest.TestCase):
    def test_build_url_routes_match_documented_endpoints(self) -> None:
        self.assertEqual(
            MODULE.build_url("https://api.example.com/", "phase2"),
            "https://api.example.com/api/v1/phase-2/deep-diagnosis",
        )
        self.assertEqual(
            MODULE.build_url("https://api.example.com", "phase2_pdf"),
            "https://api.example.com/api/v1/phase-2/deep-diagnosis/pdf",
        )
        self.assertEqual(
            MODULE.build_url("https://api.example.com", "phase3"),
            "https://api.example.com/api/v1/phase-3/optimization",
        )

    def test_call_api_retries_once_on_timeout(self) -> None:
        response = FakeResponse(
            body=json.dumps({"status": "ok"}).encode("utf-8"),
            content_type="application/json",
        )
        side_effect = [
            urllib.error.URLError(socket.timeout("timed out")),
            response,
        ]
        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=side_effect) as mocked:
            result = MODULE.call_api("https://api.example.com", None, "phase2", {"x": 1})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(mocked.call_count, 2)

    def test_download_pdf_reads_body_once_and_returns_bytes(self) -> None:
        response = FakeResponse(
            body=b"%PDF-1.4 test payload",
            content_type="application/pdf",
            disposition='attachment; filename="report.pdf"',
        )
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            body, disposition = MODULE.download_pdf("https://api.example.com", None, {"x": 1})

        self.assertEqual(body, b"%PDF-1.4 test payload")
        self.assertEqual(disposition, 'attachment; filename="report.pdf"')

    def test_download_pdf_rejects_non_pdf_body(self) -> None:
        response = FakeResponse(
            body=b'{"error":"not pdf"}',
            content_type="application/json",
        )
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(SystemExit) as raised:
                MODULE.download_pdf("https://api.example.com", None, {"x": 1})

        self.assertIn("non-pdf", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
