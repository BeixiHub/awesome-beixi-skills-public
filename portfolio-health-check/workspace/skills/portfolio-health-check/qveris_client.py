"""Minimal QVeris client for search and execute calls.

Use environment variable QVERIS_TOKEN for authentication.
This file is intentionally small and reusable by portfolio-health-check skills.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, Optional
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from date_utils import shift_months, shift_years, format_ymd

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


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
        # 单次搜索覆盖代码转换、公司信息和行业分类。
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

        # Try to auto-run company info tool for actual data
        profile_data = None
        search_id = search_result.get("search_id", "")
        _CANDIDATE_TOOLS = [
            ("mcp_gildata.companybasicinfo.v1", {"query": identifier}),
            ("ths_ifind.company_basics.v1", {"codes": identifier}),
        ]
        for target_tool_id, params in _CANDIDATE_TOOLS:
            if profile_data is not None:
                break
            matched = any(
                t.get("tool_id") == target_tool_id
                for t in search_result.get("results", [])
            )
            if not (matched and search_id):
                continue
            try:
                run_result = self._run_tool(
                    tool_id=target_tool_id,
                    search_id=search_id,
                    parameters=params,
                    session_id=session_id,
                )
                profile_data = self._extract_profile_from_result(run_result)
            except Exception:
                pass  # Try next candidate or fallback

        return {
            "identifier": identifier,
            "profile": profile_data,
            "tools_found": tools_found[:5],
        }

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
        - gildata: markdown table in result.data.results[].table_markdown
        - ths_ifind: dict rows via _flatten_result_rows
        """
        # Try gildata markdown table first
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
            rows = self._flatten_result_rows(raw)
            if rows:
                row = rows[0]
                return {
                    "ticker": (
                        row.get("ths_thscode_stock")
                        or row.get("thscode")
                        or row.get("code")
                        or ""
                    ),
                    "name": (
                        row.get("ths_corp_cn_name_stock") or row.get("name") or ""
                    ),
                    "industry": (
                        row.get("ths_the_ths_industry_stock")
                        or row.get("ths_the_sw_industry_stock")
                        or row.get("industry")
                        or ""
                    ),
                }
        except Exception:
            pass
        return None

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
        for query in plan["recommended_queries"]:
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
            "weekly_3y": self._build_ths_historySpec(
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

    def _flatten_result_rows(self, raw: Dict[str, Any]) -> list[Dict[str, Any]]:
        result = raw.get("result", {})
        data = result.get("data", {})
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            if isinstance(data.get("rows"), list):
                return [row for row in data["rows"] if isinstance(row, dict)]
            if all(isinstance(v, list) for v in data.values()) and data:
                keys = list(data.keys())
                row_count = max(len(v) for v in data.values())
                rows = []
                for idx in range(row_count):
                    rows.append(
                        {
                            key: (data[key][idx] if idx < len(data[key]) else None)
                            for key in keys
                        }
                    )
                return rows
        return []

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
            tool_search = self.search_tools(
                spec["query"],
                limit=limit,
                session_id=session_id,
            )
            search_id = tool_search.get("search_id", "")
            matched_tool = next(
                (
                    tool
                    for tool in tool_search.get("results", [])
                    if tool.get("tool_id") == spec["tool_id"]
                ),
                None,
            )
            if not (matched_tool and search_id):
                executions.append(
                    {
                        "dataset_id": spec["dataset_id"],
                        "status": "error",
                        "error": f"Tool {spec['tool_id']} not found in QVeris search",
                        "tool_search": tool_search,
                    }
                )
                continue

            execution = self.execute_tool(
                tool_id=spec["tool_id"],
                search_id=search_id,
                parameters=spec["parameters"],
                session_id=session_id,
            )
            output_path = self._artifact_output_path(spec["dataset_id"])
            output_path.write_text(
                json.dumps(execution, ensure_ascii=False, indent=2) + "\n",
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
                    "tool_search": tool_search,
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
            flat_rows = self._flatten_result_rows(payload)
            for item in flat_rows:
                rows.append(item)

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
        _print_json(result)
        return


if __name__ == "__main__":
    main()
