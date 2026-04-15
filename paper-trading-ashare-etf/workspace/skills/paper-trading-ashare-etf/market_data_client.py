"""
A 股 / ETF 行情与标的检索客户端（免鉴权）。

数据源：新浪财经公开接口。
- 名称搜索：https://suggest3.sinajs.cn/suggest/
- 实时行情：https://hq.sinajs.cn/list=

只依赖 Python 标准库，无需任何 API Key 或 token。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
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
    # Sina 接口必须带 Referer，否则返回空
    "Referer": "https://finance.sina.com.cn/",
}


@dataclass
class MarketDataConfig:
    suggest_url: str = "https://suggest3.sinajs.cn/suggest/"
    quote_url: str = "https://hq.sinajs.cn/list="
    timeout: int = 15


def _round_half_up(value: float, ndigits: int = 2) -> float:
    """四舍五入到指定小数位（避免 Python 默认的 banker's rounding）。"""
    factor = 10 ** ndigits
    return math.floor(value * factor + 0.5) / factor


class MarketDataClient:
    """A 股 / ETF 行情与标的检索。无状态、无鉴权、线程安全。"""

    def __init__(self, config: MarketDataConfig | None = None) -> None:
        self.config = config or MarketDataConfig()

    # ---------------- HTTP ----------------

    def _http_get(self, url: str) -> str:
        request = Request(url, headers=DEFAULT_HEADERS, method="GET")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            body = exc.read().decode("gb18030", errors="replace") if exc.fp else ""
            raise RuntimeError(f"MarketData HTTP error {exc.code}: {body or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"MarketData request failed: {exc.reason}") from exc
        # 新浪返回 GBK/GB18030 编码
        return raw.decode("gb18030", errors="replace")

    # ---------------- code helpers ----------------

    @staticmethod
    def _normalize_bare(stock_code: str) -> str:
        return "".join(ch for ch in stock_code.upper() if ch.isdigit())[:6]

    @staticmethod
    def to_sina_code(stock_code: str) -> str:
        """将任意格式（'600519'、'600519.SH'、'sh600519'）转为新浪格式 'sh600519'。"""
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
        # 覆盖 A 股 + ETF 全部前缀
        if bare.startswith(("6", "9", "5")):        # 沪主板 / B / 沪 ETF / 科创
            return f"sh{bare}"
        if bare.startswith(("0", "2", "3", "1")):   # 深主板 / B / 创业板 / 深 ETF
            return f"sz{bare}"
        if bare.startswith(("4", "8")):             # 北交所
            return f"bj{bare}"
        return f"sh{bare}"

    # ---------------- name → code (suggest3) ----------------

    def lookup_security_profile(self, identifier: str) -> dict[str, Any]:
        """
        按股票名或代码搜索，返回：
            {
                "identifier": <原始输入>,
                "profile":    {"ticker": "600519", "name": "贵州茅台", "industry": "" 或 "ETF"},
                "candidates": [ ... 最多 10 条候选 ... ],
            }

        Sina suggest3 type 参数：
          - 11  = A 股普通股票
          - 203 = 交易所上市 ETF（sh/sz 前缀，可直接交易）
        其它类型（港股 31 / 美股 41 / 指数 87/88 / 期货 86 / 基金产品 22/25 等）均被过滤。
        """
        # Sina suggest3 接口已改为接受 UTF-8 编码
        encoded_key = quote(identifier.encode("utf-8"))

        url = f"{self.config.suggest_url}type=11,203&key={encoded_key}"
        text = self._http_get(url)
        # 形如: var suggestdata_xxx="贵州茅台,11,600519,sh600519,贵州茅台,,贵州茅台,99;...";
        if '"' not in text:
            return {"identifier": identifier, "profile": None, "candidates": []}
        body = text.split('"', 2)[1]
        entries = [seg for seg in body.split(";") if seg.strip()]

        candidates: list[dict[str, str]] = []
        for entry in entries:
            parts = entry.split(",")
            # 格式: sina_code,type,ticker,sina_code,name,pinyin,name2,priority,flag,tags,,
            if len(parts) < 5:
                continue
            type_code = parts[1].strip()
            ticker = parts[2].strip()
            sina_code = parts[3].strip().lower()
            name = parts[4].strip()
            if not ticker or not name:
                continue
            if type_code not in {"11", "203"}:
                continue
            # 排除指数（sh000xxx / sz399xxx / bj899xxx）
            if (
                sina_code.startswith("sh000")
                or sina_code.startswith("sz399")
                or sina_code.startswith("bj899")
            ):
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
            # 仅在唯一命中时返回 profile；多候选交由上层提示用户明确选择。
            exact = [c for c in candidates if c["name"] == identifier.strip()]
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

        return {
            "identifier": identifier,
            "profile": profile,
            "candidates": candidates[:10],
        }

    # ---------------- realtime quote ----------------

    def get_realtime_quote(self, stock_code: str) -> dict[str, float] | None:
        """
        返回实时行情：
            {
                "name": "贵州茅台",
                "open", "high", "low", "latest", "preClose",
                "upperLimit", "downLimit",
                "is_st": bool,
            }

        涨跌停按规则计算：
            - ST / *ST   : ±5%
            - 科创板 688 : ±20%
            - 创业板 300 : ±20%
            - 北交所 4/8 : ±30%
            - 其它主板/ETF: ±10%

        数据源优先级：新浪 hq.sinajs.cn → 腾讯 qt.gtimg.cn
        """
        result = self._quote_from_sina(stock_code)
        if result is not None:
            return result
        return self._quote_from_tencent(stock_code)

    def _quote_from_sina(self, stock_code: str) -> dict[str, float] | None:
        """新浪行情源。"""
        sina_code = self.to_sina_code(stock_code)
        url = f"{self.config.quote_url}{sina_code}"
        try:
            text = self._http_get(url)
        except RuntimeError:
            return None
        # 形如: var hq_str_sh600519="贵州茅台,open,prev_close,current,high,low,...";
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
        except (ValueError, IndexError):
            return None
        if prev_close <= 0:
            return None
        return self._build_quote_result(sina_code, name, open_price, prev_close, latest, high, low)

    def _quote_from_tencent(self, stock_code: str) -> dict[str, float] | None:
        """腾讯行情源（qt.gtimg.cn），作为新浪不可用时的 fallback。"""
        sina_code = self.to_sina_code(stock_code)
        url = f"https://qt.gtimg.cn/q={sina_code}"
        try:
            text = self._http_get(url)
        except RuntimeError:
            return None
        # 形如: v_sz000858="51~五 粮 液~000858~103.55~103.10~102.90~..."
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
            high = float(parts[33] or 0)
            low = float(parts[34] or 0)
        except (ValueError, IndexError):
            return None
        if prev_close <= 0:
            return None
        return self._build_quote_result(sina_code, name, open_price, prev_close, latest, high, low)

    def _build_quote_result(
        self, sina_code: str, name: str,
        open_price: float, prev_close: float, latest: float,
        high: float, low: float,
    ) -> dict[str, float]:
        bare = self._normalize_bare(sina_code)
        is_st = "ST" in name.upper()
        upper_limit, down_limit = self._compute_limits(bare, prev_close, is_st)
        working_latest = latest if latest > 0 else prev_close
        return {
            "name": name,
            "open": open_price,
            "high": high,
            "low": low,
            "latest": working_latest,
            "preClose": prev_close,
            "upperLimit": upper_limit,
            "downLimit": down_limit,
            "is_st": is_st,
        }

    @staticmethod
    def _compute_limits(bare_code: str, prev_close: float, is_st: bool) -> tuple[float, float]:
        if is_st:
            pct = 0.05
        elif bare_code.startswith("688"):
            pct = 0.20
        elif bare_code.startswith("300"):
            pct = 0.20
        elif bare_code.startswith(("4", "8")):
            pct = 0.30
        else:
            pct = 0.10
        upper = _round_half_up(prev_close * (1 + pct), 2)
        lower = _round_half_up(prev_close * (1 - pct), 2)
        return upper, lower
