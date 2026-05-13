from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _find_router_env() -> Path | None:
    configured = os.getenv("DEEPSEEK_DATA_ROUTER_ENV_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    for parent in [ROOT, *ROOT.parents]:
        candidate = parent / "router_env"
        if candidate.exists():
            return candidate
    return None


def load_env(path: Path | None = None) -> None:
    configured = os.getenv("PAPER_TRADING_SKILL_ENV_PATH", "").strip()
    env_path = Path(configured).expanduser() if configured else path or ROOT / ".env"
    _load_env_file(env_path)
    router_env = _find_router_env()
    if router_env is not None:
        _load_env_file(router_env)
