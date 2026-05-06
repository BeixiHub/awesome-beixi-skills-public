from __future__ import annotations

import os
from pathlib import Path


DEFAULT_CREDENTIALS_PATH = (
    Path.home() / ".config" / "portfolio-health-check" / "credentials.env"
)


def get_credentials_path() -> Path:
    configured_path = os.getenv("PHC_CREDENTIALS_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return DEFAULT_CREDENTIALS_PATH


def _parse_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_credentials(path: str | os.PathLike[str] | None = None) -> None:
    credentials_path = Path(path).expanduser() if path else get_credentials_path()
    if not credentials_path.is_file():
        return

    with credentials_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = _parse_env_value(value)
            if key and value and not os.getenv(key):
                os.environ[key] = value
