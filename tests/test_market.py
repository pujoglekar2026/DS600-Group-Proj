import math
from datetime import datetime

import pandas as pd
import pytest

from app.industry import suggest_template, template_payload
from app.market import (DemoProvider, MarketData, MarketDataError, YahooProvider, build_fundamental_rows,
                        correlation_matrix, performance_stats, rebase)


def test_rebase_starts_at_100():
    out = rebase([("d1", 50.0), ("d2", 55.0)])
    assert out == [("d1", 100.0), ("d2", 110.0)]


def test_performance_stats():
    series = [(str(i), 100 * (1.001 ** i)) for i in range(253)]
    s = performance_stats(series)
    assert s["total_return"] == pytest.approx(1.001 ** 252 - 1)
    assert s["max_drawdown"] == 0
    assert s["annual_vol"] == pytest.approx(0, abs=1e-9)


def test_max_drawdown():
    s = performance_stats([("a", 100), ("b", 120), ("c", 90), ("d", 130)])
    assert s["max_drawdown"] == pytest.approx(90 / 120 - 1)


def test_correlation_identical_and_inverse():
    a = [(str(i), 100 + (i % 5)) for i in range(30)]
    b = [(d, p * 2) for d, p in a]
    m = correlation_matrix({"A": a, "B": b})["matrix"]
    assert m[0][1] == pytest.approx(1.0)


def test_fundamental_ratios():
    rows = build_fundamental_rows([
        {"year": 2024, "revenue": 110, "gross_profit": 44, "net_income": 11, "total_debt": 50,
         "equity": 100, "total_assets": 400, "rnd": 22},
        {"year": 2023, "revenue": 100, "net_income": 10, "equity": 90, "total_assets": 380},
    ])
    assert [r["year"] for r in rows] == [2023, 2024]
    latest = rows[-1]
    assert latest["revenue_growth"] == pytest.approx(0.10)
    assert latest["gross_margin"] == pytest.approx(0.40)
    assert latest["net_margin"] == pytest.approx(0.10)
    assert latest["debt_to_equity"] == pytest.approx(0.5)
    assert latest["roe"] == pytest.approx(0.11)
    assert latest["rnd_intensity"] == pytest.approx(0.2)
    assert rows[0]["revenue_growth"] is None  # no prior year
    assert rows[0]["debt_to_equity"] is None  # missing debt


def test_ratios_handle_zero_and_nan():
    rows = build_fundamental_rows([{"year": 2024, "revenue": 0, "net_income": float("nan"), "equity": 0}])
    assert rows[0]["net_margin"] is None and rows[0]["roe"] is None


def test_demo_provider_is_deterministic():
    d = DemoProvider()
    assert d.history("AAPL", "1y") == d.history("AAPL", "1y")
    assert len(d.history("AAPL", "3mo")) == 63
    assert d.history("AAPL", "1y")[-1] == d.history("AAPL", "5y")[-1]


def test_template_suggestion():
    assert suggest_template(["Financial Services", "Financial Services"]) == "banking"
    assert suggest_template(["Technology", "Communication Services"]) == "technology"
    assert suggest_template(["Technology", "Financial Services"]) == "general"
    tpl = template_payload("banking")
    keys = [m["key"] for m in tpl["metrics"]]
    assert "debt_to_equity" not in keys and "pb" in keys


# ---- Yahoo provider, with a fake yfinance so no network is needed ------------
class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol
        cols = [pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31")]
        self.info = {"longName": "Fake Corp", "sector": "Technology", "currency": "USD",
                     "currentPrice": 10, "trailingPE": 20.0, "dividendYield": 1.5}
        self.income_stmt = pd.DataFrame({cols[0]: [200, 80, 20], cols[1]: [160, 60, 16]},
                                        index=["Total Revenue", "Gross Profit", "Net Income"])
        self.balance_sheet = pd.DataFrame({cols[0]: [50, 100, 300], cols[1]: [40, 90, 280]},
                                          index=["Total Debt", "Stockholders Equity", "Total Assets"])

    def history(self, period, auto_adjust=True):
        idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
        return pd.DataFrame({"Close": [10.0, 11.0]}, index=idx)


class FakeYF:
    Ticker = FakeTicker


def test_yahoo_provider_parsing():
    y = YahooProvider.__new__(YahooProvider)
    y.yf = FakeYF
    assert y.history("FAKE", "1y") == [("2024-01-02", 10.0), ("2024-01-03", 11.0)]
    info = y.info("FAKE")
    assert info["name"] == "Fake Corp" and info["dividend_yield"] == pytest.approx(0.015)
    rows = y.fundamentals("FAKE")
    assert rows[-1]["year"] == 2024
    assert rows[-1]["revenue_growth"] == pytest.approx(0.25)
    assert rows[-1]["debt_to_equity"] == pytest.approx(0.5)


class BrokenProvider:
    name = "yahoo"

    def history(self, *a):
        raise ConnectionError("rate limited")


def test_fallback_to_demo():
    md = MarketData(provider="demo", fallback=True)
    md.primary = BrokenProvider()
    res = md.history("AAPL", "1mo")
    assert res["source"] == "demo" and res["data"]


def test_no_fallback_raises():
    md = MarketData(provider="demo", fallback=False)
    md.primary = BrokenProvider()
    with pytest.raises(MarketDataError):
        md.history("AAPL", "1mo")
