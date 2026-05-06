from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from credentials_utils import get_credentials_path, load_credentials  # noqa: E402


class CredentialsUtilsTests(unittest.TestCase):
    def test_loads_quoted_values_from_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / "credentials.env"
            credentials_path.write_text(
                'PORTFOLIO_API_KEY="sk test value"\n'
                "QVERIS_TOKEN='qv test value'\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {}, clear=True):
                load_credentials(credentials_path)
                self.assertEqual(os.environ["PORTFOLIO_API_KEY"], "sk test value")
                self.assertEqual(os.environ["QVERIS_TOKEN"], "qv test value")

    def test_does_not_override_existing_environment_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / "credentials.env"
            credentials_path.write_text(
                'PORTFOLIO_API_KEY="from-file"\n',
                encoding="utf-8",
            )

            with mock.patch.dict(
                os.environ,
                {"PORTFOLIO_API_KEY": "from-env"},
                clear=True,
            ):
                load_credentials(credentials_path)
                self.assertEqual(os.environ["PORTFOLIO_API_KEY"], "from-env")

    def test_get_credentials_path_respects_override(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"PHC_CREDENTIALS_PATH": "~/custom-phc/credentials.env"},
            clear=True,
        ):
            self.assertEqual(
                get_credentials_path(),
                Path.home() / "custom-phc" / "credentials.env",
            )


if __name__ == "__main__":
    unittest.main()
