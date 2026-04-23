from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))
MODULE_PATH = MODULE_DIR / "qveris_client.py"
SPEC = importlib.util.spec_from_file_location("qveris_client", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
with mock.patch.dict(sys.modules, {"pandas": mock.Mock()}):
    SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


class DownloadFullContentTests(unittest.TestCase):
    def setUp(self) -> None:
        config = MODULE.QVerisConfig(api_key="test-key", timeout=5)
        self.client = MODULE.QVerisClient(config)

    def test_rejects_file_scheme_without_fetching(self) -> None:
        payload = {"result": {"full_content_file_url": "file:///etc/passwd"}}
        with mock.patch.object(MODULE, "urlopen") as mocked:
            self.assertIsNone(self.client._download_full_content(payload))
        mocked.assert_not_called()

    def test_rejects_private_ip_without_fetching(self) -> None:
        payload = {"result": {"full_content_file_url": "http://127.0.0.1/secrets.json"}}
        with mock.patch.object(MODULE, "urlopen") as mocked:
            self.assertIsNone(self.client._download_full_content(payload))
        mocked.assert_not_called()

    def test_accepts_public_https_url(self) -> None:
        payload = {"result": {"full_content_file_url": "https://files.example.com/data.json"}}
        response = FakeResponse(b'{"rows":[{"code":"600519.SH"}]}')
        with (
            mock.patch.object(MODULE.QVerisClient, "_is_safe_resolved", return_value=True),
            mock.patch.object(MODULE, "urlopen", return_value=response),
        ):
            result = self.client._download_full_content(payload)
        self.assertEqual(result, {"rows": [{"code": "600519.SH"}]})

    def test_logs_truncated_content_parse_failure(self) -> None:
        payload = {
            "result": {
                "full_content_file_url": "https://files.example.com/data.json",
                "truncated_content": "{bad json",
            }
        }
        stderr = io.StringIO()
        with (
            mock.patch.object(MODULE.QVerisClient, "_is_safe_resolved", return_value=True),
            mock.patch.object(MODULE, "urlopen", side_effect=RuntimeError("boom")),
            mock.patch.object(MODULE.time, "sleep"),
            mock.patch.object(MODULE.sys, "stderr", stderr),
        ):
            self.assertIsNone(self.client._download_full_content(payload))
        self.assertIn("failed to parse truncated_content fallback", stderr.getvalue())

    def test_rejects_dns_rebinding_to_loopback(self) -> None:
        payload = {"result": {"full_content_file_url": "https://rebind.attacker.com/steal"}}
        with (
            mock.patch.object(MODULE.QVerisClient, "_is_safe_resolved", return_value=False),
            mock.patch.object(MODULE, "urlopen") as mocked,
        ):
            self.assertIsNone(self.client._download_full_content(payload))
        mocked.assert_not_called()

    def test_rejects_empty_dns_resolution_results(self) -> None:
        with mock.patch.object(MODULE.socket, "getaddrinfo", return_value=[]):
            self.assertFalse(self.client._is_safe_resolved("files.example.com"))


if __name__ == "__main__":
    unittest.main()
