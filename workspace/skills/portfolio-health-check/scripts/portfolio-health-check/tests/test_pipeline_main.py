"""Tests for the one-click pipeline entry."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline_main import build_pipeline_scenario, run_pipeline


class FakeQVerisClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def fetch_company_basics(self, codes, session_id: str = "", limit: int = 10) -> pd.DataFrame:
        code_list = self._codes(codes)
        self.calls.append(("fetch_company_basics", tuple(code_list), {}))
        return pd.DataFrame(
            [
                {"ticker": code, "name": f"Name-{code}", "sector_theme": "broad-equity"}
                for code in code_list
            ]
        )

    def fetch_market_caps(self, codes, session_id: str = "", limit: int = 10) -> pd.DataFrame:
        code_list = self._codes(codes)
        self.calls.append(("fetch_market_caps", tuple(code_list), {}))
        caps = {
            code_list[0]: 120_000_000_000,
            code_list[1]: 80_000_000_000,
        }
        return pd.DataFrame(
            [
                {"ticker": code, "market_cap": caps.get(code, 60_000_000_000), "name": f"MC-{code}"}
                for code in code_list
            ]
        )

    def fetch_history_quotation(
        self,
        codes,
        startdate: str,
        enddate: str,
        interval: str = "D",
        indicators: str = "close,volume",
        session_id: str = "",
        limit: int = 10,
    ) -> pd.DataFrame:
        code_list = self._codes(codes)
        self.calls.append(("fetch_history_quotation", tuple(code_list), {"interval": interval, "indicators": indicators}))
        freq = "B" if interval == "D" else "W-FRI"
        periods = 320 if interval == "D" else 180
        dates = pd.date_range("2024-01-02", periods=periods, freq=freq)
        rows = []
        for idx, code in enumerate(code_list):
            base = 100 + idx * 8
            growth = 0.0012 if code.endswith(".SH") and code != "000300.SH" else 0.0008
            for i, dt in enumerate(dates):
                rows.append(
                    {
                        "code": code,
                        "date": dt,
                        "close": round(base * ((1 + growth) ** i), 4),
                        "volume": 1_500_000 + idx * 250_000,
                    }
                )
        return pd.DataFrame(rows)

    def fetch_minute_quotation(
        self,
        codes,
        starttime: str,
        endtime: str,
        interval: str = "15",
        session_id: str = "",
        limit: int = 10,
    ) -> pd.DataFrame:
        code_list = self._codes(codes)
        self.calls.append(("fetch_minute_quotation", tuple(code_list), {"interval": interval}))
        times = pd.date_range("2026-01-02 09:30:00", periods=400, freq="15min")
        rows = []
        for idx, code in enumerate(code_list):
            base = 100 + idx * 5
            for i, dt in enumerate(times):
                rows.append(
                    {
                        "code": code,
                        "datetime": dt,
                        "close": round(base * (1 + 0.0002 * i), 4),
                        "volume": 50_000 + idx * 5_000,
                    }
                )
        return pd.DataFrame(rows)

    @staticmethod
    def _codes(codes) -> list[str]:
        if isinstance(codes, str):
            return [code.strip() for code in codes.split(",") if code.strip()]
        return [str(code).strip() for code in codes if str(code).strip()]


def test_build_pipeline_scenario_maps_api_fields():
    scenario = build_pipeline_scenario(
        {
            "rebalance_frequency": "buy_and_hold",
            "position_style": "market_timing",
            "risk_tolerance": "moderate",
            "investment_horizon": "1-3y",
        }
    )

    assert scenario["trading_frequency"] == "buy_and_hold"
    assert scenario["data_frequency"] == "daily"
    assert scenario["lookback_period"] == "5y"
    assert scenario["position_style"] == "timing_rotation"


def test_run_pipeline_generates_artifacts_with_partial_fundamentals(tmp_path: Path):
    payload = {
        "holdings": [
            {"code": "600519.SH", "weight_pct": 55.0},
            {"code": "300750.SZ", "weight_pct": 35.0},
        ],
        "cash_pct": 10.0,
        "params": {
            "rebalance_frequency": "monthly",
            "position_style": "constant_mix",
            "risk_tolerance": "moderate",
            "investment_horizon": "1-3y",
            "portfolio_market_value": 5_000_000,
        },
    }

    result = run_pipeline(
        payload,
        output_dir=tmp_path,
        client=FakeQVerisClient(),
        as_of="2026-04-01",
        include_pdf=False,
    )

    assert result["status"] == "ok"
    assert result["data"]["benchmark"]["benchmark_code"] == "000300.SH"
    assert result["data"]["metadata"]["benchmark_selection_rule"]
    assert result["data"]["factor_exposure"]["portfolio"]["value"] == 0.0
    assert result["data"]["risk_metrics"]["holdings"][0]["name"].startswith("Name-")

    artifacts = result["artifacts"]
    assert Path(artifacts["diagnosis_json"]).exists()
    assert Path(artifacts["html_report"]).exists()


def test_run_pipeline_pdf_uses_html_renderer(monkeypatch, tmp_path: Path):
    calls: dict[str, str] = {}

    def fake_convert(html_path, pdf_path, **kwargs):
        calls["html_path"] = str(html_path)
        calls["pdf_path"] = str(pdf_path)
        Path(pdf_path).write_bytes(b"%PDF-1.4\n%fake\n")
        return Path(pdf_path)

    monkeypatch.setattr("pipeline_main.convert_html_to_pdf", fake_convert)

    payload = {
        "holdings": [
            {"code": "600519.SH", "weight_pct": 60.0},
            {"code": "300750.SZ", "weight_pct": 30.0},
        ],
        "cash_pct": 10.0,
        "params": {
            "rebalance_frequency": "monthly",
            "position_style": "constant_mix",
            "risk_tolerance": "moderate",
            "investment_horizon": "1-3y",
            "portfolio_market_value": 5_000_000,
        },
    }

    result = run_pipeline(
        payload,
        output_dir=tmp_path,
        client=FakeQVerisClient(),
        as_of="2026-04-01",
        include_pdf=True,
    )

    assert result["status"] == "ok"
    artifacts = result["artifacts"]
    assert Path(artifacts["html_report"]).exists()
    assert Path(artifacts["pdf_report"]).exists()
    assert calls["html_path"].endswith("diagnosis_report.html")
    assert calls["pdf_path"].endswith("diagnosis_report.pdf")
