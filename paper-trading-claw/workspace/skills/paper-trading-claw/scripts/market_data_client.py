from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://finance.sina.com.cn/",
}


@dataclass
class MarketDataConfig:
    suggest_url: str = "https://suggest3.sinajs.cn/suggest/"
    quote_url: str = "https://hq.sinajs.cn/list="
    timeout: int = 15


def _round_half_up(value: float, ndigits: int = 2) -> float:
    quantum = Decimal(1).scaleb(-ndigits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


class MarketDataClient:
    def __init__(self, config: MarketDataConfig | None = None) -> None:
        self.config = config or MarketDataConfig()

    def _http_get(self, url: str) -> str:
        request = Request(url, headers=DEFAULT_HEADERS, method="GET")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            body = exc.read().decode("gb18030", errors="replace") if exc.fp else ""
            raise RuntimeError(f"行情接口 HTTP 错误 {exc.code}: {body or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"行情接口请求失败: {exc.reason}") from exc
        return raw.decode("gb18030", errors="replace")

    @staticmethod
    def _normalize_bare(stock_code: str) -> str:
        return "".join(ch for ch in stock_code.upper() if ch.isdigit())[:6]

    @staticmethod
    def to_sina_code(stock_code: str) -> str:
        raw = stock_code.strip().lower()
        if raw.startswith(("sh", "sz", "bj")) and len(raw) >= 8:
            return raw

        upper = stock_code.strip().upper()
        if upper.endswith(".SH"):
            return "sh" + upper[:-3]
        if upper.endswith(".SZ"):
            return "sz" + upper[:-3]
        if upper.endswith(".BJ"):
            return "bj" + upper[:-3]

        bare = MarketDataClient._normalize_bare(stock_code)
        if len(bare) != 6:
            raise ValueError(f"非法股票代码: {stock_code}")
        if bare.startswith(("6", "9", "5")):
            return f"sh{bare}"
        if bare.startswith(("0", "2", "3", "1")):
            return f"sz{bare}"
        if bare.startswith(("4", "8")):
            return f"bj{bare}"
        return f"sh{bare}"

    def lookup_security_profile(self, identifier: str) -> dict[str, Any]:
        encoded_key = quote(identifier.encode("utf-8"))
        url = f"{self.config.suggest_url}type=11,203&key={encoded_key}"
        text = self._http_get(url)
        if '"' not in text:
            return {"identifier": identifier, "profile": None, "candidates": []}

        body = text.split('"', 2)[1]
        entries = [segment for segment in body.split(";") if segment.strip()]
        candidates: list[dict[str, str]] = []
        for entry in entries:
            parts = entry.split(",")
            if len(parts) < 5:
                continue
            type_code = parts[1].strip()
            ticker = parts[2].strip()
            sina_code = parts[3].strip().lower()
            name = parts[4].strip()
            if not ticker or not name or type_code not in {"11", "203"}:
                continue
            if sina_code.startswith("sh000") or sina_code.startswith("sz399") or sina_code.startswith("bj899"):
                continue
            candidates.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "sina_code": sina_code,
                    "type_code": type_code,
                }
            )

        profile: dict[str, str] | None = None
        if candidates:
            exact = [candidate for candidate in candidates if candidate["name"] == identifier.strip()]
            chosen: dict[str, str] | None = None
            if len(exact) == 1:
                chosen = exact[0]
            elif len(candidates) == 1:
                chosen = candidates[0]
            if chosen:
                profile = {
                    "ticker": chosen["ticker"],
                    "name": chosen["name"],
                    "industry": "ETF" if chosen["type_code"] == "203" else "",
                }
        return {"identifier": identifier, "profile": profile, "candidates": candidates[:10]}

    def get_realtime_quote(self, stock_code: str) -> dict[str, float] | None:
        result = self._quote_from_sina(stock_code)
        if result is not None:
            return result
        return self._quote_from_tencent(stock_code)

    def _quote_from_sina(self, stock_code: str) -> dict[str, float] | None:
        sina_code = self.to_sina_code(stock_code)
        url = f"{self.config.quote_url}{sina_code}"
        try:
            text = self._http_get(url)
        except RuntimeError:
            return None
        if '"' not in text:
            return None
        body = text.split('"', 2)[1]
        if not body:
            return None

        parts = body.split(",")
        if len(parts) < 6:
            return None
        try:
            name = parts[0].strip()
            open_price = float(parts[1] or 0)
            prev_close = float(parts[2] or 0)
            latest = float(parts[3] or 0)
            high = float(parts[4] or 0)
            low = float(parts[5] or 0)
            bid1 = float(parts[6] or 0)
            ask1 = float(parts[7] or 0)
        except (ValueError, IndexError):
            return None
        if prev_close <= 0:
            return None
        return self._build_quote_result(sina_code, name, open_price, prev_close, latest, high, low, bid1, ask1)

    def _quote_from_tencent(self, stock_code: str) -> dict[str, float] | None:
        sina_code = self.to_sina_code(stock_code)
        url = f"https://qt.gtimg.cn/q={sina_code}"
        try:
            text = self._http_get(url)
        except RuntimeError:
            return None
        if '"' not in text:
            return None
        body = text.split('"', 2)[1]
        if not body:
            return None

        parts = body.split("~")
        if len(parts) < 35:
            return None
        try:
            name = parts[1].strip().replace(" ", "")
            latest = float(parts[3] or 0)
            prev_close = float(parts[4] or 0)
            open_price = float(parts[5] or 0)
            bid1 = float(parts[9] or 0)
            ask1 = float(parts[19] or 0)
            high = float(parts[33] or 0)
            low = float(parts[34] or 0)
        except (ValueError, IndexError):
            return None
        if prev_close <= 0:
            return None
        return self._build_quote_result(sina_code, name, open_price, prev_close, latest, high, low, bid1, ask1)

    def _build_quote_result(
        self,
        sina_code: str,
        name: str,
        open_price: float,
        prev_close: float,
        latest: float,
        high: float,
        low: float,
        bid1: float = 0.0,
        ask1: float = 0.0,
    ) -> dict[str, float]:
        bare = self._normalize_bare(sina_code)
        upper_factor = 1.1
        lower_factor = 0.9
        is_st = "ST" in name.upper()
        if is_st:
            upper_factor = 1.05
            lower_factor = 0.95
        elif bare.startswith(("688", "300")):
            upper_factor = 1.2
            lower_factor = 0.8
        elif bare.startswith(("4", "8")):
            upper_factor = 1.3
            lower_factor = 0.7

        return {
            "name": name,
            "open": _round_half_up(open_price),
            "high": _round_half_up(high),
            "low": _round_half_up(low),
            "latest": _round_half_up(latest),
            "bid1": _round_half_up(bid1),
            "ask1": _round_half_up(ask1),
            "preClose": _round_half_up(prev_close),
            "upperLimit": _round_half_up(prev_close * upper_factor),
            "downLimit": _round_half_up(prev_close * lower_factor),
            "is_st": is_st,
        }
