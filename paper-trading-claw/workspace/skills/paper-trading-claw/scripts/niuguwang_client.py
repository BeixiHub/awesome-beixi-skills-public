from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from env_loader import load_skill_env


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://swww.niuguwang.com/",
    "Origin": "https://swww.niuguwang.com",
}


@dataclass
class NiuguwangConfig:
    base_url: str = "https://apicore.niuguwang.com"
    timeout_seconds: int = 20
    aes_key_b64: str = "v1qW8F2K8mYQ7vLk2t5gGQ=="
    aes_iv_b64: str = "y3H8Kp9sT2Zx4N7Q1aBcDw=="


class NiuguwangClient:
    def __init__(self, config: NiuguwangConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def _request(self, path: str, params: dict[str, Any]) -> Any:
        url = self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        safe_params = {k: v for k, v in params.items() if v is not None}
        try:
            response = self.session.get(
                url,
                params=safe_params,
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            safe_qs = urlencode(
                [
                    (key, "<redacted>" if "token" in key.lower() else value)
                    for key, value in safe_params.items()
                ],
                safe="<>*",
            )
            raise RuntimeError(f"牛股网接口请求失败: {url}?{safe_qs} -> {exc}") from None

        try:
            return response.json()
        except ValueError:
            return {"raw": response.text, "http_status": response.status_code}

    def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        url = self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        try:
            response = self.session.post(
                url,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"牛股网注册接口请求失败: {url} -> {exc}") from None

        try:
            return response.json()
        except ValueError:
            return {"raw": response.text, "http_status": response.status_code}

    def _encrypt_mobile(self, phone: str) -> str:
        key = base64.b64decode(self.config.aes_key_b64)
        iv = base64.b64decode(self.config.aes_iv_b64)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(phone.encode("utf-8"), AES.block_size))
        return base64.b64encode(encrypted).decode("utf-8")

    def register(self, claw_token: str, phone: str) -> Any:
        return self._post_json(
            "/virtualtrade/ClawUser/Register",
            {
                "clawToken": claw_token,
                "mobile": self._encrypt_mobile(phone),
            },
        )

    def get_account(self, claw_token: str) -> Any:
        return self._request("/virtualtrade/Race/GetAccount", {"ClawToken": claw_token})

    def get_today_list(self, claw_token: str) -> Any:
        return self._request("/virtualtrade/Race/GetTodayList", {"ClawToken": claw_token})

    def get_history_list(self, claw_token: str) -> Any:
        return self._request("/virtualtrade/Race/GetHistoryList", {"ClawToken": claw_token})

    def get_today_delegate_list(self, claw_token: str) -> Any:
        return self._request("/virtualtrade/Race/GetTodayDelegateList", {"ClawToken": claw_token})

    def get_history_delegate_list(self, claw_token: str) -> Any:
        return self._request("/virtualtrade/Race/GetHistoryDelegateList", {"ClawToken": claw_token})

    def get_stock_holding_list(
        self,
        claw_token: str,
        account_id: int | str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"ClawToken": claw_token}
        if account_id is not None:
            params["accountId"] = account_id
        return self._request("/virtualtrade/Race/GetStockHoldingList", params)

    def add_delegate(
        self,
        claw_token: str,
        *,
        inner_code: int,
        amount: int,
        price: float,
        trade_type: int,
    ) -> Any:
        return self._request(
            "/virtualtrade/Race/AddDelegate",
            {
                "ClawToken": claw_token,
                "innerCode": inner_code,
                "amount": amount,
                "price": price,
                "type": trade_type,
            },
        )

    def cancel_delegate(self, claw_token: str, delegate_id: str | int) -> Any:
        return self._request(
            "/virtualtrade/Race/CancelDelegate",
            {"ClawToken": claw_token, "id": delegate_id},
        )


def build_niuguwang_client() -> NiuguwangClient:
    load_skill_env()
    return NiuguwangClient(
        NiuguwangConfig(
            base_url=os.getenv("PAPER_TRADING_NGW_BASE_URL", "https://apicore.niuguwang.com").strip(),
            timeout_seconds=int(os.getenv("PAPER_TRADING_REQUEST_TIMEOUT_SECONDS", "20")),
            aes_key_b64=os.getenv("PAPER_TRADING_NGW_AES_KEY", "v1qW8F2K8mYQ7vLk2t5gGQ==").strip(),
            aes_iv_b64=os.getenv("PAPER_TRADING_NGW_AES_IV", "y3H8Kp9sT2Zx4N7Q1aBcDw==").strip(),
        )
    )
