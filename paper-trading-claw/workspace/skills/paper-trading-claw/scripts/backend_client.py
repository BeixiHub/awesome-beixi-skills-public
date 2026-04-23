from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from .env_loader import load_skill_env


@dataclass
class BackendConfig:
    base_url: str
    internal_secret: str = ""
    timeout_seconds: int = 20


class BackendClient:
    def __init__(self, config: BackendConfig) -> None:
        if not config.base_url:
            raise ValueError("PAPER_TRADING_BACKEND_BASE_URL is not configured.")
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "paper-trading-claw-skill/2.0",
            }
        )
        if config.internal_secret:
            self.session.headers["X-Paper-Trading-Secret"] = config.internal_secret

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> Any:
        url = self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_payload,
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"后端接口请求失败: {exc}") from None

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if response.status_code >= 400:
            raise RuntimeError(f"后端接口返回 HTTP {response.status_code}: {payload}")
        return payload

    def register(self, real_name: str, phone: str, claw_token: str = "") -> Any:
        payload = {
            "realName": real_name,
            "phone": phone,
        }
        if claw_token:
            payload["clawToken"] = claw_token
        return self._request(
            "POST",
            "/api/paper-trading/register",
            json_payload=payload,
        )

    def status(self, claw_token: str) -> Any:
        return self._request(
            "GET",
            "/api/paper-trading/status",
            params={"clawToken": claw_token},
        )


def build_backend_client() -> BackendClient:
    load_skill_env()
    return BackendClient(
        BackendConfig(
            base_url=os.getenv("PAPER_TRADING_BACKEND_BASE_URL", "").strip(),
            internal_secret=os.getenv("PAPER_TRADING_BACKEND_SECRET", "").strip(),
            timeout_seconds=int(os.getenv("PAPER_TRADING_REQUEST_TIMEOUT_SECONDS", "20")),
        )
    )
