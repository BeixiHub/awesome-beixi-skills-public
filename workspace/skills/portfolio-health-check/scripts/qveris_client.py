"""Minimal QVeris client for search and execute calls.

Use environment variable QVERIS_TOKEN for authentication.
This file is intentionally small and reusable by portfolio-health-check skills.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


@dataclass
class QVerisConfig:
    api_key: str = os.getenv("QVERIS_TOKEN", "sk-FEX7dRxkDx9jc86RfiQaQfEWiEcf1IYVlYSe439sxJs")
    base_url: str = os.getenv("QVERIS_BASE_URL", "https://qveris.ai/api/v1")
    timeout: int = int(os.getenv("QVERIS_TIMEOUT", "60"))
    state_file: str = os.getenv(
        "PORTFOLIO_STATE_FILE",
        str(Path(".cursor/skills/portfolio-health-check/state/portfolio_state.json")),
    )


class QVerisClient:
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
        data = json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers=self.headers, method="POST")
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"QVeris HTTP error {exc.code}: {body or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"QVeris request failed: {exc.reason}") from exc

        return json.loads(raw)

    def search_tools(self, query: str, limit: int = 10, session_id: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {"query": query, "limit": limit}
        if session_id:
            payload["session_id"] = session_id

        url = f"{self.config.base_url}/search"
        return self._post_json(url, payload)

    def lookup_security_profile(self, identifier: str, limit: int = 5, session_id: str = "") -> Dict[str, Any]:
        queries = [
            f"company profile stock code industry classification {identifier}",
            f"stock company information industry profile {identifier}",
            f"equity profile code name industry {identifier}",
        ]
        results = []
        for query in queries:
            try:
                results.append(self.search_tools(query, limit=limit, session_id=session_id))
            except Exception as exc:
                results.append({"query": query, "error": str(exc)})
        return {"identifier": identifier, "queries": queries, "results": results}

    # ------------------------------------------------------------------
    # Direct security identification (no search step needed)
    # ------------------------------------------------------------------

    _FULL_CODE_PATTERN = re.compile(r"^(\d{6})\.([A-Z]{2})$")
    _BARE_CODE_PATTERN = re.compile(r"^\d{6}$")

    TOOL_CODE_CONVERTER = "ths_ifind.code_converter.v1"
    TOOL_COMPANY_BASICS = "ths_ifind.company_basics.v1"
    TOOL_INDUSTRY = "mcp_gildata.stockbelongindustry.v1"

    def _parse_identifier_type(self, identifier: str) -> str:
        """Return 'full_code', 'bare_code', or 'name'."""
        s = identifier.strip()
        if self._FULL_CODE_PATTERN.match(s):
            return "full_code"
        if self._BARE_CODE_PATTERN.match(s):
            return "bare_code"
        return "name"

    def _resolve_code(self, identifier: str, session_id: str = "") -> Dict[str, Any]:
        """Use code_converter: name→code or code→name.

        For full codes like '600519.SH', skip code_converter and return directly.
        """
        id_type = self._parse_identifier_type(identifier)

        if id_type == "full_code":
            return {
                "success": True,
                "result": {
                    "data": [{"table": {"thscode": [identifier.strip()]}}],
                    "metadata": {"mode": "direct", "has_results": True},
                },
            }

        if id_type == "bare_code":
            params = {"seccode": identifier.strip(), "mode": "seccode", "isexact": "0"}
            return self.execute_tool(self.TOOL_CODE_CONVERTER, "", params, session_id=session_id)

        params = {"secname": identifier, "mode": "secname", "isexact": "1"}
        result = self.execute_tool(self.TOOL_CODE_CONVERTER, "", params, session_id=session_id)

        if not result.get("success"):
            params = {"secname": identifier, "mode": "secname", "isexact": "0"}
            result = self.execute_tool(self.TOOL_CODE_CONVERTER, "", params, session_id=session_id)

        return result

    def _fetch_company_basics(self, ths_code: str, session_id: str = "") -> Dict[str, Any]:
        """Use company_basics to get name, main business, products, concepts."""
        return self.execute_tool(
            self.TOOL_COMPANY_BASICS, "", {"codes": ths_code}, session_id=session_id,
        )

    def _fetch_industry(self, name: str, session_id: str = "") -> Dict[str, Any]:
        """Use stockbelongindustry to get SW industry hierarchy."""
        return self.execute_tool(
            self.TOOL_INDUSTRY, "", {"query": f"{name}的所属申万行业"}, session_id=session_id,
        )

    def _parse_code_converter(self, raw: Dict[str, Any], identifier: str) -> Dict[str, Any]:
        """Extract codes/names from code_converter response."""
        if not raw.get("success"):
            return {"resolved": False, "error": raw.get("error_message", "unknown error")}

        data = raw.get("result", {}).get("data")
        if not data:
            return {"resolved": False, "error": "empty response"}

        entry = data[0] if isinstance(data, list) else data
        codes_raw = entry.get("table", {}).get("thscode", [])
        codes = []
        for item in codes_raw:
            codes.extend([c.strip() for c in item.split(",") if c.strip()])

        return {
            "resolved": True,
            "input": identifier,
            "codes": codes,
            "is_ambiguous": len(codes) > 1,
        }

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

    def _parse_industry(self, raw: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
        if not raw.get("success"):
            return None
        results = raw.get("result", {}).get("data", {}).get("results", [])
        if not results:
            return None
        rows = results[0].get("origin_data", {}).get("rows", [])
        return [
            {
                "level": r.get("classification", ""),
                "name": r.get("industryName", ""),
                "code": r.get("industryCode", ""),
                "standard": r.get("standard", ""),
            }
            for r in rows
        ]

    _ETF_SUFFIXES = re.compile(r"(ETF|LOF)$", re.IGNORECASE)
    _FUND_PREFIXES = ("基金", "货币", "债券型", "混合型", "指数型")

    def _looks_like_fund(self, identifier: str, ths_code: str = "") -> bool:
        if self._ETF_SUFFIXES.search(identifier):
            return True
        if any(identifier.startswith(p) for p in self._FUND_PREFIXES):
            return True
        if ths_code and ths_code.endswith((".OF", ".SZ", ".SH")):
            bare = ths_code.split(".")[0]
            if bare.startswith("1") and len(bare) == 6:
                return True
        return False

    def identify_security(self, identifier: str, session_id: str = "") -> Dict[str, Any]:
        """Identify a security end-to-end: resolve code, fetch profile & industry.

        Returns a structured dict ready for stage-1 state.
        """
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
        else:
            query_name = identifier
            if result["company"] and result["company"].get("company_name"):
                full_name = result["company"]["company_name"]
                short = full_name.replace("股份有限公司", "").replace("有限公司", "")
                query_name = short if len(short) >= 2 else identifier
            elif self._parse_identifier_type(identifier) != "name":
                query_name = primary_code

            industry_raw = self._fetch_industry(query_name, session_id)
            result["industry"] = self._parse_industry(industry_raw)

        return result

    def identify_securities(self, identifiers: List[str], session_id: str = "") -> Dict[str, Any]:
        """Batch-identify multiple securities."""
        results = []
        for ident in identifiers:
            results.append(self.identify_security(ident.strip(), session_id))
        return {"securities": results, "count": len(results)}

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

    def search_market_data_tools(self, rebalance_frequency: str, session_id: str = "", limit: int = 5) -> Dict[str, Any]:
        plan = self.build_market_data_plan(rebalance_frequency)
        results = []
        for query in plan["recommended_queries"]:
            try:
                results.append(self.search_tools(query, limit=limit, session_id=session_id))
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
        month_index = anchor.month - months
        year = anchor.year + (month_index - 1) // 12
        month = (month_index - 1) % 12 + 1
        day = min(anchor.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def _shift_years(self, anchor: date, years: int) -> date:
        year = anchor.year - years
        day = min(anchor.day, calendar.monthrange(year, anchor.month)[1])
        return date(year, anchor.month, day)

    def _start_of_year_window(self, anchor: date, years: int) -> date:
        year = anchor.year - years + 1
        return date(year, 1, 1)

    def _format_ymd(self, value: date) -> str:
        return value.strftime("%Y-%m-%d")

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

    def build_ths_coverage_plan(self, codes: str, as_of: Optional[str] = None) -> Dict[str, Any]:
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
                self._build_ths_history_spec("daily_1y", codes, self._format_ymd(one_year_start), self._format_ymd(anchor), "D", "日线数据，回溯1年"),
                self._build_ths_history_spec("weekly_1y", codes, self._format_ymd(one_year_start), self._format_ymd(anchor), "W", "周线数据，回溯1年"),
                self._build_ths_history_spec("monthly_1y", codes, self._format_ymd(one_year_start), self._format_ymd(anchor), "M", "月线数据，回溯1年"),
                self._build_ths_history_spec("daily_2y", codes, self._format_ymd(two_year_start), self._format_ymd(anchor), "D", "日线数据，回溯2年"),
                self._build_ths_history_spec("weekly_3y", codes, self._format_ymd(three_year_start), self._format_ymd(anchor), "W", "周线数据，回溯3年"),
                self._build_ths_history_spec("monthly_5y", codes, self._format_ymd(five_year_start), self._format_ymd(anchor), "M", "月线数据，回溯5年"),
                self._build_ths_history_spec("daily_3m", codes, self._format_ymd(intraday_start), self._format_ymd(anchor), "D", "日线数据，回溯3个月"),
                self._build_ths_history_spec("daily_3y", codes, self._format_ymd(three_year_start), self._format_ymd(anchor), "D", "日线数据，回溯3年"),
                self._build_ths_history_spec("daily_5y", codes, self._format_ymd(five_year_start), self._format_ymd(anchor), "D", "日线数据，回溯5年"),
            ],
        }
        return plan

    def _normalize_ths_series(self, series: Any) -> list[Dict[str, Any]]:
        normalized: list[Dict[str, Any]] = []
        for row in series:
            if not isinstance(row, dict):
                continue
            time_value = row.get("time") or row.get("date")
            normalized.append({
                "code": row.get("thscode") or row.get("security_code") or row.get("code"),
                "time": time_value,
                "close": row.get("close") or row.get("收盘价"),
                "volume": row.get("volume") or row.get("成交量"),
            })
        return normalized

    def _extract_ths_series(self, payload: Dict[str, Any]) -> list[list[Dict[str, Any]]]:
        container = payload.get("result", payload)
        data = container.get("data", []) if isinstance(container, dict) else []
        if not isinstance(data, list):
            return []
        if data and isinstance(data[0], dict):
            return [data]
        if data and isinstance(data[0], list):
            return [series for series in data if isinstance(series, list)]
        return []

    def _download_full_content(self, payload: Dict[str, Any]) -> Optional[Any]:
        container = payload.get("result", payload)
        if not isinstance(container, dict):
            return None
        full_url = container.get("full_content_file_url")
        if not full_url:
            return None
        try:
            raw = urlopen(full_url, timeout=self.config.timeout).read().decode("utf-8")
            return json.loads(raw)
        except Exception:
            truncated = container.get("truncated_content")
            if isinstance(truncated, str) and truncated:
                try:
                    return json.loads(truncated)
                except Exception:
                    return None
            return None

    def _summarize_series(self, series: list[Dict[str, Any]]) -> Dict[str, Any]:
        closes = [row["close"] for row in series if isinstance(row.get("close"), (int, float))]
        volumes = [row["volume"] for row in series if isinstance(row.get("volume"), (int, float))]
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

    def _save_artifact(self, filename: str, payload: Dict[str, Any]) -> str:
        artifact_dir = Path(self.config.state_file).parent / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / filename
        with open(artifact_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return str(artifact_path)

    def _artifact_manifest_path(self) -> Path:
        return Path(self.config.state_file).parent / "artifact_manifest.json"

    def _load_artifact_manifest(self) -> Dict[str, Any]:
        manifest_path = self._artifact_manifest_path()
        if not manifest_path.exists():
            return {}
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_artifact_manifest(self, manifest: Dict[str, Any]) -> None:
        manifest_path = self._artifact_manifest_path()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    def _clear_artifact_cache(self) -> None:
        artifact_dir = Path(self.config.state_file).parent / "artifacts"
        if not artifact_dir.exists():
            return
        for item in artifact_dir.glob("*.json"):
            try:
                item.unlink()
            except Exception:
                pass

    def _build_run_fingerprint(
        self,
        codes: str,
        rebalance_frequency: Optional[str],
        as_of: Optional[str],
        collect_all: bool,
        datasets: list[Dict[str, Any]],
    ) -> str:
        payload = {
            "format": self.ARTIFACT_FORMAT,
            "codes": codes,
            "rebalance_frequency": rebalance_frequency or "",
            "as_of": as_of or "",
            "collect_all": collect_all,
            "datasets": [
                {
                    "dataset_id": spec.get("dataset_id"),
                    "tool_id": spec.get("tool_id"),
                    "parameters": spec.get("parameters"),
                }
                for spec in datasets
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _load_artifact(self, artifact_path: Path) -> Optional[Dict[str, Any]]:
        if not artifact_path.exists():
            return None
        try:
            with open(artifact_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _artifact_matches_spec(self, artifact: Dict[str, Any], spec: Dict[str, Any]) -> bool:
        if not isinstance(artifact, dict):
            return False
        artifact_spec = artifact.get("spec", {})
        if not isinstance(artifact_spec, dict):
            return False
        return (
            artifact.get("format") == self.ARTIFACT_FORMAT
            and artifact_spec.get("dataset_id") == spec.get("dataset_id")
            and artifact_spec.get("tool_id") == spec.get("tool_id")
            and artifact_spec.get("parameters") == spec.get("parameters")
        )

    def _artifact_to_result(self, artifact: Dict[str, Any], artifact_path: Path) -> Dict[str, Any]:
        rows = artifact.get("rows", artifact.get("normalized_series", []))
        summaries = artifact.get("summaries", [])
        if not summaries and isinstance(rows, list):
            summaries = [self._summarize_series(series) for series in rows if isinstance(series, list)]
        return {
            "artifact_path": str(artifact_path),
            "summaries": summaries,
            "series_count": len(rows) if isinstance(rows, list) else 0,
            "cached": True,
        }

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
            datasets = plan["coverage"]
        else:
            if not rebalance_frequency:
                raise ValueError("rebalance_frequency is required unless collect_all is true")
            plan = self.build_ths_frequency_plan(rebalance_frequency, codes, as_of=as_of)
            datasets = plan["selected_datasets"]

        run_fingerprint = self._build_run_fingerprint(codes, rebalance_frequency, as_of, collect_all, datasets)
        manifest = self._load_artifact_manifest()
        if manifest.get("fingerprint") != run_fingerprint:
            self._clear_artifact_cache()
        self._save_artifact_manifest(
            {
                "fingerprint": run_fingerprint,
                "updated_at": self._now_iso(),
                "codes": codes,
                "rebalance_frequency": rebalance_frequency if rebalance_frequency else "",
                "as_of": as_of if as_of else "",
                "collect_all": collect_all,
            }
        )

        search_cache: Dict[str, Dict[str, Any]] = {}
        executed: list[Dict[str, Any]] = []

        for spec in datasets:
            artifact_filename = f"{spec['dataset_id']}_{codes.replace(',', '_')}.json"
            artifact_path = Path(self.config.state_file).parent / "artifacts" / artifact_filename
            cached_artifact = self._load_artifact(artifact_path)
            if cached_artifact and self._artifact_matches_spec(cached_artifact, spec):
                cached_result = self._artifact_to_result(cached_artifact, artifact_path)
                cached_result.update({
                    "dataset_id": spec["dataset_id"],
                    "tool_id": spec["tool_id"],
                    "parameters": spec["parameters"],
                })
                executed.append(cached_result)
                continue

            tool_kind = spec["tool_kind"]
            if tool_kind not in search_cache:
                query = spec["query"]
                search_cache[tool_kind] = self.search_tools(query, limit=limit, session_id=session_id)

            search_result = search_cache[tool_kind]
            chosen = None
            for tool in search_result.get("results", []):
                if tool.get("tool_id") == spec["tool_id"]:
                    chosen = tool
                    break

            if not chosen:
                executed.append({
                    "dataset_id": spec["dataset_id"],
                    "tool_id": spec["tool_id"],
                    "parameters": spec["parameters"],
                    "error": "matching tool not found in search results",
                    "search_result": search_result,
                })
                continue

            payload = self.execute_tool(
                tool_id=spec["tool_id"],
                search_id=search_result["search_id"],
                parameters=spec["parameters"],
                session_id=session_id,
            )

            full_content = self._download_full_content(payload)
            if full_content is not None and isinstance(payload.get("result"), dict):
                payload["result"]["resolved_full_content"] = full_content
                if isinstance(full_content, list):
                    payload["result"]["data"] = full_content

            series_list = self._extract_ths_series(payload)
            normalized_series = [self._normalize_ths_series(series) for series in series_list]
            summaries = [self._summarize_series(series) for series in normalized_series]
            artifact_path = self._save_artifact(
                artifact_filename,
                {
                    "format": self.ARTIFACT_FORMAT,
                    "spec": spec,
                    "search_id": search_result["search_id"],
                    "tool_id": spec["tool_id"],
                    "parameters": spec["parameters"],
                    "rows": normalized_series,
                    "summaries": summaries,
                },
            )

            executed.append({
                "dataset_id": spec["dataset_id"],
                "tool_id": spec["tool_id"],
                "parameters": spec["parameters"],
                "search_id": search_result["search_id"],
                "artifact_path": artifact_path,
                "summaries": summaries,
                "series_count": len(normalized_series),
            })

        state = self.load_state()
        state["version"] = "1.0"
        state["stage2"] = {
            "parameters": state.get("stage2", {}).get("parameters", {}),
            "codes": codes,
            "collect_all": collect_all,
            "rebalance_frequency": rebalance_frequency if rebalance_frequency else "",
            "reference_date": plan["reference_date"],
            "selected_dataset_ids": plan.get("selected_dataset_ids", []),
            "datasets": executed,
            "saved_at": self._now_iso(),
            "cache": {
                "fingerprint": run_fingerprint,
                "manifest_path": str(self._artifact_manifest_path()),
            },
        }
        state["updated_at"] = self._now_iso()
        self.save_state(state)
        return state["stage2"]

    def load_state(self) -> Dict[str, Any]:
        state_path = Path(self.config.state_file)
        if not state_path.exists():
            return {
                "version": "1.0",
                "updated_at": "",
                "stage1": {},
                "stage2": {},
            }
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state_path = Path(self.config.state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return state

    def update_stage1_state(self, identifier: str, lookup_result: Dict[str, Any]) -> Dict[str, Any]:
        state = self.load_state()
        state["version"] = "1.0"
        state["stage1"] = {
            "identifier": identifier,
            "lookup_result": lookup_result,
            "saved_at": self._now_iso(),
        }
        state["updated_at"] = self._now_iso()
        return self.save_state(state)

    def update_stage2_state(self, rebalance_frequency: str, market_result: Dict[str, Any]) -> Dict[str, Any]:
        state = self.load_state()
        state["version"] = "1.0"
        state["stage2"] = {
            "rebalance_frequency": rebalance_frequency,
            "market_result": market_result,
            "saved_at": self._now_iso(),
        }
        state["updated_at"] = self._now_iso()
        return self.save_state(state)

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


def _print_json(data: Dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="QVeris client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search QVeris tools")
    search_parser.add_argument("query", help="Natural language tool query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--session-id", default="")

    lookup_parser = subparsers.add_parser("lookup", help="Lookup stock/company profile tools (legacy)")
    lookup_parser.add_argument("identifier", help="Stock name or code to identify")
    lookup_parser.add_argument("--limit", type=int, default=5)
    lookup_parser.add_argument("--session-id", default="")

    identify_parser = subparsers.add_parser("identify", help="Identify security: resolve code, profile & industry")
    identify_parser.add_argument("identifiers", nargs="+", help="One or more stock names or codes")
    identify_parser.add_argument("--session-id", default="")

    market_parser = subparsers.add_parser("market-plan", help="Build market data plan by rebalance frequency")
    market_parser.add_argument("rebalance_frequency", help="intraday|weekly|monthly|quarterly|buy_and_hold")

    market_search_parser = subparsers.add_parser("market-search", help="Search market data tools by rebalance frequency")
    market_search_parser.add_argument("rebalance_frequency", help="intraday|weekly|monthly|quarterly|buy_and_hold")
    market_search_parser.add_argument("--limit", type=int, default=5)
    market_search_parser.add_argument("--session-id", default="")

    ths_collect_parser = subparsers.add_parser("ths-collect", help="Collect THS quotation data and persist it")
    ths_collect_parser.add_argument("codes", help="Comma-separated THS codes")
    ths_collect_parser.add_argument("--rebalance-frequency", default="", help="intraday|weekly|monthly|quarterly|buy_and_hold")
    ths_collect_parser.add_argument("--as-of", dest="as_of", default="", help="Reference date in YYYY-MM-DD")
    ths_collect_parser.add_argument("--all", action="store_true", help="Collect all supported THS timeframes")
    ths_collect_parser.add_argument("--session-id", default="")
    ths_collect_parser.add_argument("--limit", type=int, default=10)

    state_parser = subparsers.add_parser("state", help="Inspect saved structured state")
    state_subparsers = state_parser.add_subparsers(dest="state_command", required=True)
    state_show_parser = state_subparsers.add_parser("show", help="Show current saved state")
    state_path_parser = state_subparsers.add_parser("path", help="Print state file path")

    execute_parser = subparsers.add_parser("execute", help="Execute a QVeris tool")
    execute_parser.add_argument("tool_id", help="Tool id from search results")
    execute_parser.add_argument("search_id", help="Search id from the search call")
    execute_parser.add_argument("parameters", nargs="?", help="JSON string of tool parameters")
    execute_parser.add_argument("--parameters-json", dest="parameters_json", help="JSON string of tool parameters")
    execute_parser.add_argument("--parameters-file", dest="parameters_file", help="Path to a JSON file of tool parameters")
    execute_parser.add_argument("--session-id", default="")
    execute_parser.add_argument("--max-response-size", type=int, default=102400)

    args = parser.parse_args()
    client = QVerisClient()

    if args.command == "search":
        result = client.search_tools(args.query, limit=args.limit, session_id=args.session_id)
        _print_json(result)
        return

    if args.command == "lookup":
        result = client.lookup_security_profile(args.identifier, limit=args.limit, session_id=args.session_id)
        client.update_stage1_state(args.identifier, result)
        _print_json(result)
        return

    if args.command == "identify":
        if len(args.identifiers) == 1:
            result = client.identify_security(args.identifiers[0], session_id=args.session_id)
        else:
            result = client.identify_securities(args.identifiers, session_id=args.session_id)
        client.update_stage1_state(
            ",".join(args.identifiers),
            result,
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

    if args.command == "execute":
        params_source = args.parameters_json or args.parameters
        if args.parameters_file:
            with open(args.parameters_file, "r", encoding="utf-8-sig") as f:
                params_source = f.read()
        if not params_source:
            raise SystemExit("Provide parameters with --parameters-json, --parameters-file, or the positional parameters argument")

        params = json.loads(params_source.lstrip("\ufeff"))
        result = client.execute_tool(
            tool_id=args.tool_id,
            search_id=args.search_id,
            parameters=params,
            session_id=args.session_id,
            max_response_size=args.max_response_size,
        )
        _print_json(result)


if __name__ == "__main__":
    main()
