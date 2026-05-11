"""Minimal QVeris client for search and execute calls.

Use environment variable QVERIS_TOKEN for authentication.
This file is intentionally small and reusable by portfolio-health-check skills.
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from credentials_utils import load_credentials
from date_utils import shift_months, shift_years, format_ymd

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


load_credentials()

HANGSENG_SECURITY_PROFILE_TOOL_ID = (
    "hangseng_polysource.stock.basicCorpInfo.retrieve.v2.d7c68583"
)
HANGSENG_SECURITY_PROFILE_TOOL_PREFIX = (
    "hangseng_polysource.stock.basicCorpInfo.retrieve.v2."
)
HANGSENG_SECURITY_PROFILE_QUERY_TOOL_ID = (
    "hangseng_polysource.stock.basicCorpInfo.query.v2.d7c68583"
)
HANGSENG_SECURITY_PROFILE_QUERY_TOOL_PREFIX = (
    "hangseng_polysource.stock.basicCorpInfo.query.v2."
)


@dataclass
class QVerisConfig:
    # 统一从环境变量读取连接配置，便于本地、CI 和生产环境切换。
    api_key: str = os.getenv("QVERIS_TOKEN", "")
    base_url: str = os.getenv("QVERIS_BASE_URL", "https://qveris.ai/api/v1")
    timeout: int = int(os.getenv("QVERIS_TIMEOUT", "60"))
    state_file: str = os.getenv(
        "PORTFOLIO_STATE_FILE",
        str(Path("state/portfolio_state.json")),
    )


class QVerisClient:
    # 这个版本号用于缓存产物和结果结构的一致性校验。
    ARTIFACT_FORMAT = "lean-v3"

    def __init__(self, config: Optional[QVerisConfig] = None) -> None:
        self.config = config or QVerisConfig()
        if not self.config.api_key:
            raise ValueError("QVERIS_TOKEN is not set")

        self.headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 所有接口请求都统一走这里，集中处理编码、超时和异常转换。
        data = json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers=self.headers, method="POST")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(
                f"QVeris HTTP error {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"QVeris request failed: {exc.reason}") from exc

        return json.loads(raw)

    def search_tools(
        self, query: str, limit: int = 10, session_id: str = ""
    ) -> Dict[str, Any]:
        # 将自然语言查询交给 QVeris 的搜索接口，返回候选工具列表。
        payload: Dict[str, Any] = {"query": query, "limit": limit}
        if session_id:
            payload["session_id"] = session_id

        url = f"{self.config.base_url}/search"
        return self._post_json(url, payload)

    def lookup_security_profile(
        self, identifier: str, limit: int = 5, session_id: str = ""
    ) -> Dict[str, Any]:
        # 优先走直接识别链路；识别失败时再回退到搜索驱动模式。
        try:
            identified = self.identify_security(identifier, session_id=session_id)
            if identified.get("code_lookup", {}).get("resolved"):
                profile = self._profile_from_identification(identified)
                result = {
                    "identifier": identifier,
                    "profile": profile,
                    "tools_found": [],
                    "code_lookup": identified.get("code_lookup", {}),
                    "asset_type": identified.get("asset_type", ""),
                    "company": identified.get("company"),
                    "industry": identified.get("industry"),
                }
                if identified.get("industry_note"):
                    result["industry_note"] = identified["industry_note"]
                return result
        except Exception as exc:
            print(
                f"[qveris] direct identification failed for {identifier!r}: {exc}",
                file=sys.stderr,
            )

        return self._lookup_security_profile_via_search(
            identifier,
            limit=limit,
            session_id=session_id,
        )

    def _lookup_security_profile_via_search(
        self, identifier: str, limit: int = 5, session_id: str = ""
    ) -> Dict[str, Any]:
        query = f"stock code company profile industry {identifier}"
        search_result = self.search_tools(query, limit=limit, session_id=session_id)

        tools_found = []
        for tool in search_result.get("results", []):
            tools_found.append(
                {
                    "tool_id": tool.get("tool_id", ""),
                    "name": tool.get("name", ""),
                    "description": tool.get("description", "")[:120],
                }
            )

        profile_data = None
        search_id = search_result.get("search_id", "")
        candidate_tools = [
            (
                HANGSENG_SECURITY_PROFILE_TOOL_ID,
                HANGSENG_SECURITY_PROFILE_TOOL_PREFIX,
                {"StockObject": [identifier], "pageNo": 1, "pageSize": 10},
            ),
            (
                HANGSENG_SECURITY_PROFILE_QUERY_TOOL_ID,
                HANGSENG_SECURITY_PROFILE_QUERY_TOOL_PREFIX,
                {"StockObject": [identifier], "pageNo": 1, "pageSize": 10},
            ),
            ("ths_ifind.company_basics.v1", "", {"codes": identifier}),
        ]
        search_tools = search_result.get("results", [])
        for target_tool_id, tool_prefix, params in candidate_tools:
            if profile_data is not None:
                break
            matched_tool_id = self._match_tool_id(
                search_tools, target_tool_id, tool_prefix
            )
            if not (matched_tool_id and search_id):
                continue
            try:
                run_result = self._run_tool(
                    tool_id=matched_tool_id,
                    search_id=search_id,
                    parameters=params,
                    session_id=session_id,
                )
                profile_data = self._extract_profile_from_result(run_result)
            except Exception:
                pass

        return {
            "identifier": identifier,
            "profile": profile_data,
            "tools_found": tools_found[:5],
        }

    @staticmethod
    def _match_tool_id(
        tools: List[Dict[str, Any]], exact_tool_id: str, tool_prefix: str = ""
    ) -> str:
        for tool in tools:
            tool_id = tool.get("tool_id", "")
            if tool_id == exact_tool_id:
                return tool_id
        if tool_prefix:
            for tool in tools:
                tool_id = tool.get("tool_id", "")
                if tool_id.startswith(tool_prefix):
                    return tool_id
        return ""

    def _run_tool(
        self,
        tool_id: str,
        search_id: str,
        parameters: Dict[str, Any],
        session_id: str = "",
    ) -> Dict[str, Any]:
        """Thin wrapper around execute_tool for lookup_security_profile."""
        return self.execute_tool(
            tool_id=tool_id,
            search_id=search_id,
            parameters=parameters,
            session_id=session_id,
        )

    def _extract_profile_from_result(
        self, raw: Dict[str, Any]
    ) -> Optional[Dict[str, str]]:
        """Extract ticker/name/industry from tool execution result.

        Handles two formats:
        - polysource: markdown table in result.data.results[].table_markdown
        - ths_ifind: dict rows via _flatten_result_rows
        """
        # Try polysource markdown table first.
        try:
            results_list = raw.get("result", {}).get("data", {}).get("results", [])
            for item in results_list:
                md = item.get("table_markdown", "")
                if not md:
                    continue
                lines = [l.strip() for l in md.strip().split("\n") if l.strip()]
                if len(lines) < 3:
                    continue
                headers = [h.strip() for h in lines[0].split("|")]
                values = [v.strip() for v in lines[2].split("|")]
                if headers and not headers[0]:
                    headers = headers[1:]
                if headers and not headers[-1]:
                    headers = headers[:-1]
                if values and not values[0]:
                    values = values[1:]
                if values and not values[-1]:
                    values = values[:-1]
                while len(values) < len(headers):
                    values.append("")
                row = dict(zip(headers, values[: len(headers)]))
                ticker = row.get("股票代码", "")
                name = row.get("股票名称", "") or row.get("中文名称", "")
                industry = (
                    row.get("所属申万行业", "")
                    or row.get("所属证监会行业", "")
                    or row.get("所属中信行业", "")
                )
                if ticker or name:
                    return {"ticker": ticker, "name": name, "industry": industry}
        except Exception:
            pass

        try:
            row = self._find_profile_row(raw)
            if row:
                return row
        except Exception:
            pass

        try:
            rows = self._flatten_result_rows(raw)
            if rows:
                row = rows[0]
                return {
                    "ticker": (
                        row.get("ths_thscode_stock")
                        or row.get("StockCode")
                        or row.get("SecuCode")
                        or row.get("secuCode")
                        or row.get("thscode")
                        or row.get("code")
                        or ""
                    ),
                    "name": (
                        row.get("ths_corp_cn_name_stock")
                        or row.get("SecuAbbr")
                        or row.get("ChiName")
                        or row.get("CompanyName")
                        or row.get("name")
                        or ""
                    ),
                    "industry": (
                        row.get("ths_the_ths_industry_stock")
                        or row.get("ths_the_sw_industry_stock")
                        or row.get("IndustryName")
                        or row.get("Industry")
                        or row.get("industry")
                        or ""
                    ),
                }
        except Exception:
            pass
        return None

    def _find_profile_row(self, payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
        best_row: Optional[Dict[str, str]] = None
        best_score = 0
        for row in self._walk_dicts(payload):
            lower = {str(k).lower(): v for k, v in row.items()}
            if self._looks_like_schema_row(lower):
                continue
            ticker = (
                lower.get("stockcode")
                or lower.get("secucode")
                or lower.get("stockobject")
                or lower.get("code")
                or ""
            )
            name = (
                lower.get("stockname")
                or lower.get("chiname")
                or lower.get("secuabbr")
                or lower.get("secabbr")
                or lower.get("companyname")
                or lower.get("name")
                or ""
            )
            industry = (
                lower.get("industrysw")
                or lower.get("industryzjh")
                or lower.get("industryzx")
                or lower.get("industryjy")
                or lower.get("industryname")
                or lower.get("industry")
                or ""
            )
            if self._looks_like_label_row(ticker, name, industry):
                continue
            score = int(bool(ticker)) + int(bool(name)) * 2 + int(bool(industry))
            if score > best_score:
                best_score = score
                best_row = {
                    "ticker": str(ticker or ""),
                    "name": str(name or ""),
                    "industry": str(industry or ""),
                }
        return best_row if best_score >= 2 else None

    @staticmethod
    def _looks_like_schema_row(row: Dict[str, Any]) -> bool:
        if not row:
            return False
        type_markers = {
            "string",
            "int",
            "integer",
            "double",
            "float",
            "date",
            "datetime",
            "boolean",
        }
        values = [str(value).strip().lower() for value in row.values()]
        marker_count = sum(1 for value in values if value in type_markers)
        return marker_count >= 3 and marker_count >= len(values) * 0.8

    @staticmethod
    def _looks_like_label_row(ticker: Any, name: Any, industry: Any) -> bool:
        labels = {"股票代码", "股票名称", "所属申万行业", "所属证监会行业", "所属中信行业"}
        values = {str(value).strip() for value in (ticker, name, industry) if value}
        return bool(values) and values.issubset(labels)

    def _walk_dicts(self, value: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_dicts(child)

    _FULL_CODE_PATTERN = re.compile(r"^(\d{6})\.([A-Z]{2})$")
    _BARE_CODE_PATTERN = re.compile(r"^\d{6}$")
    _ETF_SUFFIXES = re.compile(r"(ETF|LOF)$", re.IGNORECASE)
    _FUND_PREFIXES = ("基金", "货币", "债券型", "混合型", "指数型")

    TOOL_CODE_CONVERTER = "ths_ifind.code_converter.v1"
    TOOL_COMPANY_BASICS = "ths_ifind.company_basics.v1"
    TOOL_INDUSTRY = HANGSENG_SECURITY_PROFILE_TOOL_ID
    TOOL_REALTIME_QUOTE = "ths_ifind.real_time_quotation.v1"

    def _parse_identifier_type(self, identifier: str) -> str:
        value = identifier.strip()
        if self._FULL_CODE_PATTERN.match(value):
            return "full_code"
        if self._BARE_CODE_PATTERN.match(value):
            return "bare_code"
        return "name"

    def _resolve_code(
        self, identifier: str, session_id: str = ""
    ) -> Dict[str, Any]:
        identifier = identifier.strip()
        id_type = self._parse_identifier_type(identifier)

        if id_type == "full_code":
            return {
                "success": True,
                "result": {
                    "data": [{"table": {"thscode": [identifier]}}],
                    "metadata": {"mode": "direct", "has_results": True},
                },
            }

        if id_type == "bare_code":
            return self.execute_tool(
                self.TOOL_CODE_CONVERTER,
                "",
                {"seccode": identifier, "mode": "seccode", "isexact": "0"},
                session_id=session_id,
            )

        result = self.execute_tool(
            self.TOOL_CODE_CONVERTER,
            "",
            {"secname": identifier, "mode": "secname", "isexact": "1"},
            session_id=session_id,
        )
        if not result.get("success"):
            result = self.execute_tool(
                self.TOOL_CODE_CONVERTER,
                "",
                {"secname": identifier, "mode": "secname", "isexact": "0"},
                session_id=session_id,
            )
        return result

    def _parse_code_converter(
        self, raw: Dict[str, Any], identifier: str
    ) -> Dict[str, Any]:
        if not raw.get("success"):
            return {
                "resolved": False,
                "error": raw.get("error_message", "unknown error"),
            }

        data = raw.get("result", {}).get("data")
        if not data:
            return {"resolved": False, "error": "empty response"}

        entry = data[0] if isinstance(data, list) else data
        codes_raw = entry.get("table", {}).get("thscode", [])
        codes: List[str] = []
        for item in codes_raw:
            codes.extend([code.strip() for code in item.split(",") if code.strip()])

        if not codes:
            return {
                "resolved": False,
                "error": f"No codes found for identifier: {identifier}",
                "input": identifier,
                "codes": [],
            }

        return {
            "resolved": True,
            "input": identifier,
            "codes": codes,
            "is_ambiguous": len(codes) > 1,
        }

    def _fetch_company_basics(
        self, ths_code: str, session_id: str = ""
    ) -> Dict[str, Any]:
        return self.execute_tool(
            self.TOOL_COMPANY_BASICS,
            "",
            {"codes": ths_code},
            session_id=session_id,
        )

    def _parse_company_basics(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not raw.get("success"):
            return None
        data = raw.get("result", {}).get("data")
        if not data:
            return None
        rows = data[0] if isinstance(data, list) and data else data
        if isinstance(rows, list) and rows:
            row = rows[0]
        elif isinstance(rows, dict):
            row = rows
        else:
            return None
        return {
            "ths_code": row.get("ths_thscode_stock") or row.get("thscode", ""),
            "stock_code": row.get("ths_stock_code_stock", ""),
            "company_name": row.get("ths_corp_cn_name_stock", ""),
            "main_business": row.get("ths_main_businuess_stock", ""),
            "products": row.get("ths_mo_product_name_stock", ""),
            "concepts": row.get("ths_the_ths_concept_index_stock", ""),
            "established_date": row.get("ths_established_date_stock", ""),
        }

    def _fetch_industry(self, name: str, session_id: str = "") -> Dict[str, Any]:
        return self.execute_tool(
            self.TOOL_INDUSTRY,
            "",
            {"StockObject": [name], "pageNo": 1, "pageSize": 10},
            session_id=session_id,
        )

    def _parse_industry(
        self, raw: Dict[str, Any]
    ) -> Optional[List[Dict[str, str]]]:
        if not raw.get("success"):
            return None
        profile = self._find_profile_row(raw)
        if profile and profile.get("industry"):
            return [
                {
                    "level": "申万行业",
                    "name": profile["industry"],
                    "code": "",
                    "standard": "申万",
                }
            ]
        results = raw.get("result", {}).get("data", {}).get("results", [])
        if not results:
            return None
        entry = results[0] if isinstance(results[0], dict) else {}
        origin_data = entry.get("origin_data")
        if not isinstance(origin_data, dict):
            return None
        rows = origin_data.get("rows", [])
        if not rows:
            return None
        return [
            {
                "level": row.get("classification", ""),
                "name": row.get("industryName", ""),
                "code": row.get("industryCode", ""),
                "standard": row.get("standard", ""),
            }
            for row in rows
        ]

    def _looks_like_fund(self, identifier: str, ths_code: str = "") -> bool:
        if self._ETF_SUFFIXES.search(identifier):
            return True
        if any(identifier.startswith(prefix) for prefix in self._FUND_PREFIXES):
            return True
        if ths_code and ths_code.endswith((".OF", ".SZ", ".SH")):
            bare = ths_code.split(".")[0]
            if bare.startswith("1") and len(bare) == 6:
                return True
        return False

    def _profile_from_identification(
        self, result: Dict[str, Any]
    ) -> Optional[Dict[str, str]]:
        company = result.get("company") or {}
        industry_rows = result.get("industry") or []
        industry = ""
        if industry_rows:
            industry = industry_rows[-1].get("name", "")
        elif result.get("industry_note"):
            industry = result["industry_note"]

        ticker = (
            company.get("ths_code")
            or result.get("primary_code", "")
            or result.get("code_lookup", {}).get("codes", [""])[0]
        )
        name = company.get("company_name", "")
        if not (ticker or name or industry):
            return None
        return {"ticker": ticker, "name": name, "industry": industry}

    def identify_security(
        self, identifier: str, session_id: str = ""
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {"identifier": identifier}

        code_raw = self._resolve_code(identifier, session_id)
        code_info = self._parse_code_converter(code_raw, identifier)
        result["code_lookup"] = code_info

        if not code_info.get("resolved"):
            result["company"] = None
            result["industry"] = None
            return result

        primary_code = code_info["codes"][0]
        result["primary_code"] = primary_code

        basics_raw = self._fetch_company_basics(primary_code, session_id)
        result["company"] = self._parse_company_basics(basics_raw)

        is_fund = self._looks_like_fund(identifier, primary_code)
        result["asset_type"] = "fund_or_etf" if is_fund else "stock"

        if is_fund:
            result["industry"] = None
            result["industry_note"] = "ETF/基金类标的不适用个股行业分类"
            return result

        query_name = identifier
        company = result.get("company") or {}
        if company.get("company_name"):
            short_name = (
                company["company_name"]
                .replace("股份有限公司", "")
                .replace("有限公司", "")
            )
            query_name = short_name if len(short_name) >= 2 else identifier
        elif self._parse_identifier_type(identifier) != "name":
            query_name = primary_code

        industry_raw = self._fetch_industry(query_name, session_id)
        result["industry"] = self._parse_industry(industry_raw)
        return result

    def fetch_realtime_quotes(
        self, codes: Iterable[str], session_id: str = ""
    ) -> Dict[str, Any]:
        code_list = [str(code).strip() for code in codes if str(code).strip()]
        raw = self.execute_tool(
            self.TOOL_REALTIME_QUOTE,
            "",
            {"codes": ",".join(code_list), "indicators": "latest,preClose"},
            session_id=session_id,
        )

        quotes: Dict[str, Any] = {}
        if not raw.get("success"):
            return {
                "success": False,
                "error": raw.get("error_message", "unknown"),
                "quotes": quotes,
            }

        for series in raw.get("result", {}).get("data", []):
            if not isinstance(series, list) or not series:
                continue
            entry = series[0]
            code = entry.get("thscode", "")
            quotes[code] = {
                "code": code,
                "latest_price": entry.get("最新价"),
                "pre_close": entry.get("前收盘价"),
                "time": entry.get("time", ""),
            }

        return {"success": True, "quotes": quotes}

    def compute_portfolio_weights(
        self,
        holdings: List[Dict[str, Any]],
        cash: float = 0.0,
        session_id: str = "",
    ) -> Dict[str, Any]:
        codes = [holding["code"] for holding in holdings if holding.get("code")]
        if not codes:
            return {"success": False, "error": "no valid codes provided"}

        quote_result = self.fetch_realtime_quotes(codes, session_id=session_id)
        if not quote_result.get("success"):
            return quote_result

        quotes = quote_result["quotes"]
        enriched = []
        total_equity_value = 0.0
        for holding in holdings:
            code = holding.get("code", "")
            shares = holding.get("shares", 0)
            quote = quotes.get(code)
            if quote and quote.get("latest_price") is not None:
                price = float(quote["latest_price"])
                market_value = price * shares
            else:
                price = None
                market_value = None

            row = {
                "name": holding.get("name", ""),
                "code": code,
                "shares": shares,
                "latest_price": price,
                "market_value": market_value,
            }
            enriched.append(row)
            if market_value is not None:
                total_equity_value += market_value

        total_value = total_equity_value + cash
        for row in enriched:
            if row["market_value"] is not None and total_value > 0:
                row["weight_pct"] = round(row["market_value"] / total_value * 100, 2)
            else:
                row["weight_pct"] = None

        quote_missing_count = sum(
            1 for row in enriched if row["market_value"] is None
        )
        all_quotes_missing = bool(enriched) and quote_missing_count == len(enriched)

        result = {
            "success": not (all_quotes_missing and cash == 0),
            "holdings": enriched,
            "cash": {
                "amount": cash,
                "weight_pct": round(cash / total_value * 100, 2)
                if total_value > 0
                else None,
            },
            "total_equity_value": round(total_equity_value, 2),
            "total_value": round(total_value, 2),
            "quote_time": next(
                (quote["time"] for quote in quotes.values() if quote.get("time")),
                "",
            ),
        }
        if all_quotes_missing:
            result["warning"] = "realtime quotes missing for all holdings"
            if cash == 0:
                result["error"] = "unable to compute weights because all realtime quotes are missing"

        return result

    def build_market_data_plan(self, rebalance_frequency: str) -> Dict[str, Any]:
        plans = {
            "intraday": {
                "primary": {"interval": "15m", "lookback": "3mo"},
                "secondary": {"interval": "1d", "lookback": "1y"},
            },
            "weekly": {
                "primary": {"interval": "1d", "lookback": "1y"},
                "secondary": None,
            },
            "monthly": {
                "primary": {"interval": "1d", "lookback": "2y"},
                "secondary": None,
            },
            "quarterly": {
                "primary": {"interval": "1wk", "lookback": "3y"},
                "secondary": None,
            },
            "buy_and_hold": {
                "primary": {"interval": "1mo", "lookback": "5y"},
                "secondary": None,
            },
        }

        if rebalance_frequency not in plans:
            raise ValueError(f"Unsupported rebalance_frequency: {rebalance_frequency}")

        return {
            "rebalance_frequency": rebalance_frequency,
            "fields": ["close", "volume"],
            "plan": plans[rebalance_frequency],
            "recommended_queries": [
                f"historical price close volume API {plans[rebalance_frequency]['primary']['interval']} {plans[rebalance_frequency]['primary']['lookback']}",
                f"stock price volume data API close {rebalance_frequency}",
            ],
        }

    def search_market_data_tools(
        self, rebalance_frequency: str, session_id: str = "", limit: int = 5
    ) -> Dict[str, Any]:
        plan = self.build_market_data_plan(rebalance_frequency)
        results = []
        for query in plan.get("recommended_queries", []):
            try:
                results.append(
                    self.search_tools(query, limit=limit, session_id=session_id)
                )
            except Exception as exc:
                results.append({"query": query, "error": str(exc)})
        return {"plan": plan, "results": results}

    def _parse_date(self, value: Optional[str] = None) -> date:
        if value:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
                try:
                    return datetime.strptime(value, fmt).date()
                except ValueError:
                    continue
            raise ValueError(f"Unsupported date format: {value}")
        return datetime.now().date()

    def _shift_months(self, anchor: date, months: int) -> date:
        return shift_months(anchor, months)

    def _shift_years(self, anchor: date, years: int) -> date:
        return shift_years(anchor, years)

    def _start_of_year_window(self, anchor: date, years: int) -> date:
        year = anchor.year - years + 1
        return date(year, 1, 1)

    def _format_ymd(self, value: date) -> str:
        return format_ymd(value)

    def _build_ths_history_spec(
        self,
        dataset_id: str,
        codes: str,
        startdate: str,
        enddate: str,
        interval: str,
        note: str,
    ) -> Dict[str, Any]:
        return {
            "dataset_id": dataset_id,
            "tool_id": "ths_ifind.history_quotation.v1",
            "query": "ths_ifind history quotation daily weekly monthly stock",
            "note": note,
            "parameters": {
                "codes": codes,
                "startdate": startdate,
                "enddate": enddate,
                "interval": interval,
                "indicators": "close,volume",
            },
            "tool_kind": "history",
        }

    def _build_ths_minute_spec(
        self,
        dataset_id: str,
        codes: str,
        starttime: str,
        endtime: str,
        interval: str = "15",
        note: str = "",
    ) -> Dict[str, Any]:
        return {
            "dataset_id": dataset_id,
            "tool_id": "ths_ifind.hf_basic_quotation.v1",
            "query": "ths_ifind hf basic quotation 15 minute",
            "note": note,
            "parameters": {
                "codes": codes,
                "starttime": starttime,
                "endtime": endtime,
                "interval": interval,
            },
            "tool_kind": "minute",
        }

    def build_ths_frequency_plan(
        self,
        rebalance_frequency: str,
        codes: str,
        as_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        anchor = self._parse_date(as_of)
        intraday_start = self._shift_months(anchor, 3)
        one_year_start = self._start_of_year_window(anchor, 1)
        two_year_start = self._start_of_year_window(anchor, 2)
        three_year_start = self._start_of_year_window(anchor, 3)
        five_year_start = self._start_of_year_window(anchor, 5)

        coverage = {
            "minute_15m_3m": self._build_ths_minute_spec(
                "minute_15m_3m",
                codes,
                f"{self._format_ymd(intraday_start)} 09:30:00",
                f"{self._format_ymd(anchor)} 15:00:00",
                interval="15",
                note="15分钟数据，回溯3个月",
            ),
            "daily_1y": self._build_ths_history_spec(
                "daily_1y",
                codes,
                self._format_ymd(one_year_start),
                self._format_ymd(anchor),
                "D",
                "日线数据，回溯1年",
            ),
            "weekly_1y": self._build_ths_history_spec(
                "weekly_1y",
                codes,
                self._format_ymd(one_year_start),
                self._format_ymd(anchor),
                "W",
                "周线数据，回溯1年",
            ),
            "monthly_1y": self._build_ths_history_spec(
                "monthly_1y",
                codes,
                self._format_ymd(one_year_start),
                self._format_ymd(anchor),
                "M",
                "月线数据，回溯1年",
            ),
            "daily_2y": self._build_ths_history_spec(
                "daily_2y",
                codes,
                self._format_ymd(two_year_start),
                self._format_ymd(anchor),
                "D",
                "日线数据，回溯2年",
            ),
            "weekly_3y": self._build_ths_history_spec(
                "weekly_3y",
                codes,
                self._format_ymd(three_year_start),
                self._format_ymd(anchor),
                "W",
                "周线数据，回溯3年",
            ),
            "monthly_5y": self._build_ths_history_spec(
                "monthly_5y",
                codes,
                self._format_ymd(five_year_start),
                self._format_ymd(anchor),
                "M",
                "月线数据，回溯5年",
            ),
        }

        matrix = {
            "intraday": ["minute_15m_3m", "daily_1y"],
            "weekly": ["daily_1y"],
            "monthly": ["daily_2y"],
            "quarterly": ["weekly_3y"],
            "buy_and_hold": ["monthly_5y"],
        }

        if rebalance_frequency not in matrix:
            raise ValueError(f"Unsupported rebalance_frequency: {rebalance_frequency}")

        selected_ids = matrix[rebalance_frequency]
        selected = [coverage[dataset_id] for dataset_id in selected_ids]

        return {
            "rebalance_frequency": rebalance_frequency,
            "reference_date": self._format_ymd(anchor),
            "matrix": matrix,
            "coverage": coverage,
            "selected_dataset_ids": selected_ids,
            "selected_datasets": selected,
        }

    def build_ths_coverage_plan(
        self, codes: str, as_of: Optional[str] = None
    ) -> Dict[str, Any]:
        anchor = self._parse_date(as_of)
        intraday_start = self._shift_months(anchor, 3)
        one_year_start = self._start_of_year_window(anchor, 1)
        two_year_start = self._start_of_year_window(anchor, 2)
        three_year_start = self._start_of_year_window(anchor, 3)
        five_year_start = self._start_of_year_window(anchor, 5)
        plan = {
            "reference_date": self._format_ymd(anchor),
            "coverage": [
                self._build_ths_minute_spec(
                    "minute_15m_3m",
                    codes,
                    f"{self._format_ymd(intraday_start)} 09:30:00",
                    f"{self._format_ymd(anchor)} 15:00:00",
                    interval="15",
                    note="15分钟数据，回溯3个月",
                ),
                self._build_ths_history_spec(
                    "daily_1y",
                    codes,
                    self._format_ymd(one_year_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯1年",
                ),
                self._build_ths_history_spec(
                    "weekly_1y",
                    codes,
                    self._format_ymd(one_year_start),
                    self._format_ymd(anchor),
                    "W",
                    "周线数据，回溯1年",
                ),
                self._build_ths_history_spec(
                    "monthly_1y",
                    codes,
                    self._format_ymd(one_year_start),
                    self._format_ymd(anchor),
                    "M",
                    "月线数据，回溯1年",
                ),
                self._build_ths_history_spec(
                    "daily_2y",
                    codes,
                    self._format_ymd(two_year_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯2年",
                ),
                self._build_ths_history_spec(
                    "weekly_3y",
                    codes,
                    self._format_ymd(three_year_start),
                    self._format_ymd(anchor),
                    "W",
                    "周线数据，回溯3年",
                ),
                self._build_ths_history_spec(
                    "monthly_5y",
                    codes,
                    self._format_ymd(five_year_start),
                    self._format_ymd(anchor),
                    "M",
                    "月线数据，回溯5年",
                ),
                self._build_ths_history_spec(
                    "daily_3m",
                    codes,
                    self._format_ymd(intraday_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯3个月",
                ),
                self._build_ths_history_spec(
                    "daily_3y",
                    codes,
                    self._format_ymd(three_year_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯3年",
                ),
                self._build_ths_history_spec(
                    "daily_5y",
                    codes,
                    self._format_ymd(five_year_start),
                    self._format_ymd(anchor),
                    "D",
                    "日线数据，回溯5年",
                ),
            ],
        }
        return plan

    def execute_tool(
        self,
        tool_id: str,
        search_id: str,
        parameters: Dict[str, Any],
        session_id: str = "",
        max_response_size: int = 102400,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "search_id": search_id,
            "parameters": parameters,
            "max_response_size": max_response_size,
        }
        if session_id:
            payload["session_id"] = session_id

        url = f"{self.config.base_url}/tools/execute?tool_id={tool_id}"
        return self._post_json(url, payload)

    def _download_full_content(self, payload: Dict[str, Any]) -> Optional[Any]:
        result = payload.get("result", payload)
        if not isinstance(result, dict):
            return None

        full_url = result.get("full_content_file_url")
        if not full_url:
            return None
        if not self._is_safe_full_content_url(full_url):
            print(
                f"[qveris] WARNING: rejected unsafe full_content_file_url: {full_url}",
                file=sys.stderr,
            )
            return None

        last_error: Optional[Exception] = None
        timeout = max(self.config.timeout, 120)
        for attempt in range(3):
            try:
                raw = urlopen(full_url, timeout=timeout).read().decode("utf-8")
                return json.loads(raw)
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(attempt + 1)

        print(
            f"[qveris] WARNING: failed to download full content after 3 attempts: {last_error}",
            file=sys.stderr,
        )
        print(f"[qveris] full_content_file_url: {full_url}", file=sys.stderr)

        truncated = result.get("truncated_content")
        if isinstance(truncated, str) and truncated:
            try:
                return json.loads(truncated)
            except ValueError as exc:
                print(
                    f"[qveris] WARNING: failed to parse truncated_content fallback: {exc}",
                    file=sys.stderr,
                )
                return None
        return None

    @staticmethod
    def _is_safe_resolved(hostname: str) -> bool:
        """Resolve hostname via DNS and reject private/loopback/reserved IPs.

        Prevents DNS rebinding: a hostname that passed the parse-time check
        could resolve to 127.0.0.1 at connect time if the attacker controls
        DNS with TTL=0.
        """
        try:
            results = socket.getaddrinfo(hostname, None)
            if not results:
                return False
            for family, _, _, _, addr in results:
                ip = ipaddress.ip_address(addr[0])
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_reserved
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    return False
        except socket.gaierror:
            return False
        return True

    def _is_safe_full_content_url(self, full_url: str) -> bool:
        parsed = urlparse(full_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        if parsed.username or parsed.password:
            return False

        hostname = parsed.hostname
        if not hostname:
            return False
        normalized = hostname.lower()
        if normalized == "localhost" or normalized.endswith(".local"):
            return False

        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            # hostname is a domain name, not an IP literal — resolve it
            return self._is_safe_resolved(normalized)

        if (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_private
            or address.is_reserved
            or address.is_unspecified
        ):
            return False
        return True

    def resolve_full_content(self, payload: Dict[str, Any]) -> Optional[Any]:
        return self._download_full_content(payload)

    def _flatten_result_rows(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = raw.get("result", raw)
        data = result.get("data", []) if isinstance(result, dict) else []
        if not data:
            full_content = self._download_full_content(raw)
            if isinstance(full_content, list):
                data = full_content

        rows: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    rows.append(item)
                elif isinstance(item, list):
                    rows.extend(row for row in item if isinstance(row, dict))
            return rows

        if isinstance(data, dict):
            if isinstance(data.get("rows"), list):
                return [row for row in data["rows"] if isinstance(row, dict)]
            if all(isinstance(value, list) for value in data.values()) and data:
                keys = list(data.keys())
                row_count = max(len(value) for value in data.values())
                for idx in range(row_count):
                    rows.append(
                        {
                            key: (data[key][idx] if idx < len(data[key]) else None)
                            for key in keys
                        }
                    )
        return rows

    def _normalize_ths_series(self, series: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not isinstance(series, list):
            return normalized

        for row in series:
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    "code": row.get("thscode")
                    or row.get("security_code")
                    or row.get("code"),
                    "time": row.get("time") or row.get("date"),
                    "close": row.get("close") or row.get("收盘价"),
                    "volume": row.get("volume") or row.get("成交量"),
                }
            )
        return normalized

    def _extract_ths_series(self, payload: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
        result = payload.get("result", payload)
        data = result.get("data", []) if isinstance(result, dict) else []
        if not data:
            full_content = self._download_full_content(payload)
            if isinstance(full_content, list):
                data = full_content

        if not isinstance(data, list):
            return []
        if data and isinstance(data[0], dict):
            return [data]
        if data and isinstance(data[0], list):
            return [series for series in data if isinstance(series, list)]
        return []

    def _split_series_by_code(
        self, series_list: List[List[Dict[str, Any]]]
    ) -> List[List[Dict[str, Any]]]:
        result: List[List[Dict[str, Any]]] = []
        for series in series_list:
            codes_in_series = {
                row.get("code", "") for row in series if isinstance(row, dict)
            }
            if len(codes_in_series) <= 1:
                result.append(series)
                continue

            by_code: Dict[str, List[Dict[str, Any]]] = {}
            for row in series:
                code = row.get("code", "")
                by_code.setdefault(code, []).append(row)
            result.extend(by_code.values())
        return result

    def _summarize_series(self, series: List[Dict[str, Any]]) -> Dict[str, Any]:
        closes = [
            row["close"]
            for row in series
            if isinstance(row.get("close"), (int, float))
        ]
        volumes = [
            row["volume"]
            for row in series
            if isinstance(row.get("volume"), (int, float))
        ]
        return {
            "start": series[0]["time"] if series else "",
            "end": series[-1]["time"] if series else "",
            "rows": len(series),
            "first_close": closes[0] if closes else None,
            "last_close": closes[-1] if closes else None,
            "min_close": min(closes) if closes else None,
            "max_close": max(closes) if closes else None,
            "min_volume": min(volumes) if volumes else None,
            "max_volume": max(volumes) if volumes else None,
            "last_volume": volumes[-1] if volumes else None,
        }

    def load_state(self) -> Dict[str, Any]:
        path = Path(self.config.state_file)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_state(self, state: Dict[str, Any]) -> None:
        path = Path(self.config.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def update_stage1_state(self, identifier: str, lookup_result: Dict[str, Any]) -> None:
        state = self.load_state()
        stage1 = state.setdefault("stage1", {})
        lookups = stage1.setdefault("lookups", {})
        lookups[identifier] = lookup_result
        stage1["artifact_format"] = self.ARTIFACT_FORMAT
        self.save_state(state)

    def update_stage2_state(
        self, rebalance_frequency: str, market_search_result: Dict[str, Any]
    ) -> None:
        state = self.load_state()
        stage2 = state.setdefault("stage2", {})
        stage2["rebalance_frequency"] = rebalance_frequency
        stage2["market_search_result"] = market_search_result
        stage2["artifact_format"] = self.ARTIFACT_FORMAT
        self.save_state(state)

    def _artifact_output_path(self, dataset_id: str) -> Path:
        state_path = Path(self.config.state_file)
        artifacts_dir = state_path.parent / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        return artifacts_dir / f"{dataset_id}.json"

    def collect_ths_datasets(
        self,
        codes: str,
        rebalance_frequency: Optional[str] = None,
        as_of: Optional[str] = None,
        collect_all: bool = False,
        session_id: str = "",
        limit: int = 10,
    ) -> Dict[str, Any]:
        if collect_all:
            plan = self.build_ths_coverage_plan(codes, as_of=as_of)
            dataset_specs = plan["coverage"]
        else:
            if not rebalance_frequency:
                raise ValueError(
                    "rebalance_frequency is required unless collect_all=True"
                )
            plan = self.build_ths_frequency_plan(
                rebalance_frequency,
                codes,
                as_of=as_of,
            )
            dataset_specs = plan["selected_datasets"]

        executions = []
        manifest = []
        for spec in dataset_specs:
            try:
                execution = self.execute_tool(
                    tool_id=spec["tool_id"],
                    search_id="",
                    parameters=spec["parameters"],
                    session_id=session_id,
                )
            except Exception as exc:
                executions.append(
                    {
                        "dataset_id": spec["dataset_id"],
                        "status": "error",
                        "tool_id": spec["tool_id"],
                        "error": str(exc),
                    }
                )
                continue

            if not execution.get("success", True):
                error_message = execution.get("error_message") or execution.get("error") or "tool execution failed"
                executions.append(
                    {
                        "dataset_id": spec["dataset_id"],
                        "status": "error",
                        "tool_id": spec["tool_id"],
                        "error": error_message,
                    }
                )
                continue

            full_content = self._download_full_content(execution)
            has_full_url = bool(
                isinstance(execution.get("result"), dict)
                and execution["result"].get("full_content_file_url")
            )
            if full_content is not None and isinstance(execution.get("result"), dict):
                execution["result"]["resolved_full_content"] = full_content
                if isinstance(full_content, list):
                    execution["result"]["data"] = full_content

            series_list = self._extract_ths_series(execution)
            normalized_series = [
                self._normalize_ths_series(series) for series in series_list
            ]
            normalized_series = self._split_series_by_code(normalized_series)
            summaries = [
                self._summarize_series(series) for series in normalized_series
            ]

            output_path = self._artifact_output_path(spec["dataset_id"])
            artifact_payload = {
                "format": self.ARTIFACT_FORMAT,
                "spec": spec,
                "raw_result": execution,
                "rows": normalized_series,
                "summaries": summaries,
            }
            output_path.write_text(
                json.dumps(artifact_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            manifest_entry = {
                "dataset_id": spec["dataset_id"],
                "tool_id": spec["tool_id"],
                "path": str(output_path),
                "note": spec.get("note", ""),
                "tool_kind": spec.get("tool_kind", ""),
                "parameters": spec["parameters"],
            }
            manifest.append(manifest_entry)
            executions.append(
                {
                    "dataset_id": spec["dataset_id"],
                    "status": "ok",
                    "path": str(output_path),
                    "tool_id": spec["tool_id"],
                    "series_count": len(normalized_series),
                    "summaries": summaries,
                    "full_content_resolved": has_full_url
                    and full_content is not None,
                }
            )

        state = self.load_state()
        stage2 = state.setdefault("stage2", {})
        ths_data = {
            "codes": codes,
            "as_of": as_of or plan.get("reference_date"),
            "collect_all": collect_all,
            "plan": plan,
            "manifest": manifest,
            "executions": executions,
            "artifact_format": self.ARTIFACT_FORMAT,
        }
        stage2["ths_data"] = ths_data
        self.save_state(state)

        return ths_data

    def export_close_volume_csv(
        self, output_path: str, artifact_paths: Optional[Iterable[str]] = None
    ) -> Dict[str, Any]:
        state = self.load_state()
        stage2 = state.get("stage2", {})
        ths_data = stage2.get("ths_data", {})
        manifest = ths_data.get("manifest", [])

        if artifact_paths:
            source_paths = [Path(p) for p in artifact_paths]
        else:
            source_paths = [Path(item["path"]) for item in manifest if item.get("path")]

        rows: list[dict[str, Any]] = []
        for path in source_paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            series_rows = payload.get("rows")
            if isinstance(series_rows, list):
                for series in series_rows:
                    if not isinstance(series, list):
                        continue
                    is_hf = (
                        "minute" in path.name
                        or payload.get("spec", {}).get("tool_kind") == "minute"
                    )
                    time_key = "datetime" if is_hf else "date"
                    for item in series:
                        if not isinstance(item, dict):
                            continue
                        rows.append(
                            {
                                "code": item.get("code", ""),
                                time_key: item.get("time", ""),
                                "close": item.get("close", ""),
                                "volume": item.get("volume", ""),
                            }
                        )
                continue

            flat_rows = self._flatten_result_rows(payload)
            rows.extend(item for item in flat_rows if isinstance(item, dict))

        fieldnames = sorted({key for row in rows for key in row.keys()})
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="") as file_obj:
            writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        return {
            "output_path": output_path,
            "rows": len(rows),
            "columns": fieldnames,
        }


def _print_json(data: Dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="QVeris client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search QVeris tools")
    search_parser.add_argument("query", help="Natural language tool query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--session-id", default="")

    lookup_parser = subparsers.add_parser(
        "lookup", aliases=["identify"], help="Lookup stock/company profile tools"
    )
    lookup_parser.add_argument(
        "identifier", nargs="+", help="Stock name(s) or code(s) to identify"
    )
    lookup_parser.add_argument("--limit", type=int, default=5)
    lookup_parser.add_argument("--session-id", default="")

    quote_parser = subparsers.add_parser(
        "quote", help="Fetch real-time prices; with shares compute portfolio weights"
    )
    quote_parser.add_argument(
        "holdings",
        nargs="+",
        help="NAME=CODE:SHARES (e.g. 贵州茅台=600519.SH:100) or just codes",
    )
    quote_parser.add_argument("--cash", type=float, default=0.0)
    quote_parser.add_argument("--session-id", default="")

    market_parser = subparsers.add_parser(
        "market-plan", help="Build market data plan by rebalance frequency"
    )
    market_parser.add_argument(
        "rebalance_frequency", help="intraday|weekly|monthly|quarterly|buy_and_hold"
    )

    market_search_parser = subparsers.add_parser(
        "market-search", help="Search market data tools by rebalance frequency"
    )
    market_search_parser.add_argument(
        "rebalance_frequency", help="intraday|weekly|monthly|quarterly|buy_and_hold"
    )
    market_search_parser.add_argument("--limit", type=int, default=5)
    market_search_parser.add_argument("--session-id", default="")

    ths_collect_parser = subparsers.add_parser(
        "ths-collect", help="Collect THS quotation data and persist it"
    )
    ths_collect_parser.add_argument("codes", help="Comma-separated THS codes")
    ths_collect_parser.add_argument(
        "--rebalance-frequency",
        default="",
        help="intraday|weekly|monthly|quarterly|buy_and_hold",
    )
    ths_collect_parser.add_argument(
        "--as-of", dest="as_of", default="", help="Reference date in YYYY-MM-DD"
    )
    ths_collect_parser.add_argument(
        "--all", action="store_true", help="Collect all supported THS timeframes"
    )
    ths_collect_parser.add_argument("--session-id", default="")
    ths_collect_parser.add_argument("--limit", type=int, default=10)

    state_parser = subparsers.add_parser("state", help="Inspect saved structured state")
    state_subparsers = state_parser.add_subparsers(dest="state_command", required=True)
    state_subparsers.add_parser("show", help="Show current saved state")
    state_subparsers.add_parser("path", help="Print state file path")

    export_parser = subparsers.add_parser(
        "export-csv", help="Export close/volume rows to CSV"
    )
    export_parser.add_argument("--output", required=True, help="Output CSV path")
    export_parser.add_argument(
        "--artifacts",
        nargs="*",
        default=[],
        help="Optional artifact json paths; if omitted, use stage2.ths_data datasets",
    )

    execute_parser = subparsers.add_parser("execute", help="Execute a QVeris tool")
    execute_parser.add_argument("tool_id", help="Tool id from search results")
    execute_parser.add_argument("search_id", help="Search id from the search call")
    execute_parser.add_argument(
        "parameters", nargs="?", help="JSON string of tool parameters"
    )
    execute_parser.add_argument(
        "--parameters-json",
        dest="parameters_json",
        help="JSON string of tool parameters",
    )
    execute_parser.add_argument(
        "--parameters-file",
        dest="parameters_file",
        help="Path to a JSON file of tool parameters",
    )
    execute_parser.add_argument("--session-id", default="")
    execute_parser.add_argument("--max-response-size", type=int, default=102400)

    args = parser.parse_args()
    client = QVerisClient()

    if args.command == "search":
        result = client.search_tools(
            args.query, limit=args.limit, session_id=args.session_id
        )
        _print_json(result)
        return

    if args.command in ("lookup", "identify"):
        results = []
        for ident in args.identifier:
            r = client.lookup_security_profile(
                ident, limit=args.limit, session_id=args.session_id
            )
            client.update_stage1_state(ident, r)
            results.append(r)
        _print_json(results if len(results) > 1 else results[0])
        return

    if args.command == "quote":
        has_shares = any(":" in holding for holding in args.holdings)
        if has_shares:
            holdings = []
            for holding in args.holdings:
                name_part, _, code_shares = holding.partition("=")
                if not code_shares:
                    code_shares = name_part
                    name_part = ""
                parts = code_shares.split(":", 1)
                code = parts[0].strip()
                try:
                    shares = float(parts[1]) if len(parts) > 1 else 0
                except ValueError:
                    invalid_value = parts[1] if len(parts) > 1 else ""
                    print(
                        f"[qveris] WARNING: invalid shares value '{invalid_value}' for code '{code}', defaulting to 0",
                        file=sys.stderr,
                    )
                    shares = 0
                holdings.append(
                    {
                        "code": code,
                        "shares": shares,
                        "name": name_part.strip() or code,
                    }
                )
            result = client.compute_portfolio_weights(
                holdings,
                cash=args.cash,
                session_id=args.session_id,
            )
        else:
            result = client.fetch_realtime_quotes(
                args.holdings,
                session_id=args.session_id,
            )
        _print_json(result)
        return

    if args.command == "market-plan":
        result = client.build_market_data_plan(args.rebalance_frequency)
        _print_json(result)
        return

    if args.command == "market-search":
        result = client.search_market_data_tools(
            args.rebalance_frequency,
            session_id=args.session_id,
            limit=args.limit,
        )
        client.update_stage2_state(args.rebalance_frequency, result)
        _print_json(result)
        return

    if args.command == "ths-collect":
        result = client.collect_ths_datasets(
            codes=args.codes,
            rebalance_frequency=args.rebalance_frequency or None,
            as_of=args.as_of or None,
            collect_all=args.all,
            session_id=args.session_id,
            limit=args.limit,
        )
        _print_json(result)
        return

    if args.command == "state":
        if args.state_command == "show":
            _print_json(client.load_state())
            return
        if args.state_command == "path":
            print(client.config.state_file)
            return

    if args.command == "export-csv":
        result = client.export_close_volume_csv(
            output_path=args.output,
            artifact_paths=args.artifacts,
        )
        _print_json(result)
        return

    if args.command == "execute":
        parameters: Dict[str, Any] = {}
        if args.parameters_json:
            parameters = json.loads(args.parameters_json)
        elif args.parameters_file:
            parameters = json.loads(Path(args.parameters_file).read_text(encoding="utf-8"))
        elif args.parameters:
            parameters = json.loads(args.parameters)

        result = client.execute_tool(
            tool_id=args.tool_id,
            search_id=args.search_id,
            parameters=parameters,
            session_id=args.session_id,
            max_response_size=args.max_response_size,
        )
        full_content = client.resolve_full_content(result)
        if full_content is not None and isinstance(result.get("result"), dict):
            result["result"]["data"] = full_content
            result["result"].pop("truncated_content", None)
            result["result"].pop("full_content_file_url", None)
        _print_json(result)
        return


if __name__ == "__main__":
    main()
