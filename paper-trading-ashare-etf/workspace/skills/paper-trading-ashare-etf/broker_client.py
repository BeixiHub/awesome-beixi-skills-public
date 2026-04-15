from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


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
class BrokerConfig:
    user_token: str
    account_id: str = ""
    base_url: str = "https://swww.niuguwang.com"
    timeout: int = 30
    contest: int = 1
    share: int = 0


class BrokerClient:
    def __init__(self, config: BrokerConfig) -> None:
        self.config = config
        if not self.config.user_token:
            raise ValueError("Missing broker user_token")

    def _build_url(self, path: str, params: dict[str, Any]) -> str:
        base = urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
        pairs = [(k, v) for k, v in params.items() if v is not None]
        query = urlencode(pairs, safe="*")
        return f"{base}?{query}" if query else base

    def request_json(self, path: str, params: dict[str, Any]) -> Any:
        url = self._build_url(path, params)
        request = Request(url, headers=DEFAULT_HEADERS, method="GET")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Broker HTTP error {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Broker request failed: {exc.reason}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    def get_account(self) -> Any:
        return self.request_json(
            "/virtual/api/Account/Get",
            {"userToken": self.config.user_token},
        )

    def get_stock_holding(self) -> Any:
        params = {"userToken": self.config.user_token}
        if self.config.account_id:
            params["accountId"] = self.config.account_id
        return self.request_json("/virtual/api/StockHolding/Get", params)

    def get_stock_deal_today_list(self) -> Any:
        return self.request_json(
            "/virtual/api/StockDeal/GetTodayList",
            {"userToken": self.config.user_token},
        )

    def get_stock_deal_history_list(self) -> Any:
        return self.request_json(
            "/virtual/api/StockDeal/GetHistoryList",
            {"userToken": self.config.user_token},
        )

    def get_stock_delegate_today_list(self) -> Any:
        return self.request_json(
            "/virtual/api/StockDelegate/GetTodayList",
            {"userToken": self.config.user_token},
        )

    def get_stock_delegate_history_list(self) -> Any:
        return self.request_json(
            "/virtual/api/StockDelegate/GetHistoryList",
            {"userToken": self.config.user_token},
        )

    def delegate_buy_stock(self, inner_code: int | str, amount: int, price: float) -> Any:
        return self.request_json(
            "/tr/delegateadd.ashx",
            {
                "userToken": self.config.user_token,
                "innerCode": inner_code,
                "amount": amount,
                "price": price,
                "type": 1,
                "contest": self.config.contest,
                "share": self.config.share,
            },
        )

    def delegate_sell_stock(self, inner_code: int | str, amount: int, price: float) -> Any:
        return self.request_json(
            "/tr/delegateadd.ashx",
            {
                "userToken": self.config.user_token,
                "innerCode": inner_code,
                "amount": amount,
                "price": price,
                "type": 2,
                "contest": self.config.contest,
                "share": self.config.share,
            },
        )

    def delegate_cancel(self, delegate_id: int | str) -> Any:
        return self.request_json(
            "/tr/delegatecancel.ashx",
            {
                "userToken": self.config.user_token,
                "id": delegate_id,
            },
        )
