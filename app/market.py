"""Market data: stock prices, quotes and financial fundamentals.

Two providers share one interface:
  * YahooProvider - live data from Yahoo Finance via the yfinance library
  * DemoProvider  - deterministic synthetic data, so the app always works
                    (offline, in tests, or when Yahoo rate-limits a cloud IP)

`MarketData` picks the provider, caches results in memory, and falls back to
demo data when DEMO_FALLBACK is on. Every result carries a `source` field so
the UI can tell the user when they are looking at demo data.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import threading
import time
from datetime import date, timedelta

log = logging.getLogger(__name__)

PERIODS = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}


class MarketDataError(Exception):
    pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _safe_div(a, b):
    try:
        if a is None or b is None or b == 0 or (isinstance(b, float) and math.isnan(b)):
            return None
        value = a / b
        return None if math.isnan(value) else value
    except (TypeError, ValueError):
        return None


def _clean(value):
    try:
        if value is None:
            return None
        value = float(value)
        return None if math.isnan(value) or math.isinf(value) else value
    except (TypeError, ValueError):
        return None


def build_fundamental_rows(years: list[dict]) -> list[dict]:
    """Turn raw yearly statement figures into ratios (oldest year first).

    Each input dict may hold: year, revenue, gross_profit, net_income, rnd,
    total_debt, equity, total_assets.
    """
    years = sorted(years, key=lambda y: y["year"])
    rows, prev_rev = [], None
    for y in years:
        rev = _clean(y.get("revenue"))
        rows.append({
            "year": y["year"],
            "revenue": rev,
            "net_income": _clean(y.get("net_income")),
            "revenue_growth": _safe_div((rev - prev_rev) if rev is not None and prev_rev is not None else None, abs(prev_rev) if prev_rev else None),
            "gross_margin": _safe_div(_clean(y.get("gross_profit")), rev),
            "net_margin": _safe_div(_clean(y.get("net_income")), rev),
            "rnd_intensity": _safe_div(_clean(y.get("rnd")), rev),
            "debt_to_equity": _safe_div(_clean(y.get("total_debt")), _clean(y.get("equity"))),
            "roe": _safe_div(_clean(y.get("net_income")), _clean(y.get("equity"))),
            "roa": _safe_div(_clean(y.get("net_income")), _clean(y.get("total_assets"))),
            "equity_to_assets": _safe_div(_clean(y.get("equity")), _clean(y.get("total_assets"))),
        })
        prev_rev = rev
    return rows


# --------------------------------------------------------------------------
# Demo provider
# --------------------------------------------------------------------------
DEMO_COMPANIES = {
    "AAPL": ("Apple Inc.", "Technology", 190.0),
    "MSFT": ("Microsoft Corporation", "Technology", 420.0),
    "GOOGL": ("Alphabet Inc.", "Communication Services", 170.0),
    "NVDA": ("NVIDIA Corporation", "Technology", 120.0),
    "JPM": ("JPMorgan Chase & Co.", "Financial Services", 210.0),
    "BAC": ("Bank of America Corporation", "Financial Services", 40.0),
    "GS": ("The Goldman Sachs Group, Inc.", "Financial Services", 480.0),
    "D05.SI": ("DBS Group Holdings Ltd", "Financial Services", 36.0),
    "O39.SI": ("Oversea-Chinese Banking Corp", "Financial Services", 15.0),
    "U11.SI": ("United Overseas Bank Ltd", "Financial Services", 32.0),
    "KO": ("The Coca-Cola Company", "Consumer Defensive", 65.0),
    "WMT": ("Walmart Inc.", "Consumer Defensive", 70.0),
    "XOM": ("Exxon Mobil Corporation", "Energy", 115.0),
}


class DemoProvider:
    name = "demo"

    @staticmethod
    def _rng(ticker: str, salt: str = "") -> random.Random:
        seed = int(hashlib.sha256(f"{ticker}{salt}".encode()).hexdigest()[:12], 16)
        return random.Random(seed)

    def _meta(self, ticker):
        if ticker in DEMO_COMPANIES:
            return DEMO_COMPANIES[ticker]
        rng = self._rng(ticker, "meta")
        sector = rng.choice(["Technology", "Financial Services", "Consumer Defensive", "Industrials"])
        return (f"{ticker} (demo company)", sector, round(rng.uniform(20, 400), 2))

    def info(self, ticker: str) -> dict:
        name, sector, _ = self._meta(ticker)
        rng = self._rng(ticker, "info")
        bank = sector == "Financial Services"
        return {
            "ticker": ticker,
            "name": name,
            "sector": sector,
            "industry": "Banks - Diversified" if bank else sector,
            "currency": "SGD" if ticker.endswith(".SI") else "USD",
            "market_cap": rng.uniform(2e10, 3e12),
            "pe": rng.uniform(8, 14) if bank else rng.uniform(15, 40),
            "pb": rng.uniform(0.8, 1.8) if bank else rng.uniform(3, 15),
            "dividend_yield": rng.uniform(0.03, 0.06) if bank else rng.uniform(0.0, 0.03),
            "beta": rng.uniform(0.6, 1.5),
        }

    def history(self, ticker: str, period: str) -> list[tuple[str, float]]:
        n = PERIODS.get(period, 252)
        _, _, start_price = self._meta(ticker)
        rng = self._rng(ticker, "px")
        drift, vol = rng.uniform(-0.0002, 0.0009), rng.uniform(0.010, 0.025)
        # Walk the full 5y path so shorter periods are consistent subsets.
        total = PERIODS["5y"]
        price, prices = start_price * 0.6, []
        for _ in range(total):
            price *= math.exp(drift - 0.5 * vol**2 + vol * rng.gauss(0, 1))
            prices.append(price)
        days, d = [], date.today()
        while len(days) < total:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        days.reverse()
        series = list(zip([x.isoformat() for x in days], [round(p, 4) for p in prices]))
        return series[-n:]

    def fundamentals(self, ticker: str) -> list[dict]:
        _, sector, _ = self._meta(ticker)
        rng = self._rng(ticker, "fund")
        bank = sector == "Financial Services"
        tech = sector == "Technology"
        revenue = rng.uniform(2e10, 3e11)
        assets = revenue * (25 if bank else 1.5)
        years, this_year = [], date.today().year - 1
        for i in range(4, -1, -1):
            revenue *= 1 + rng.uniform(-0.03, 0.18 if tech else 0.08)
            equity = assets * (rng.uniform(0.07, 0.10) if bank else rng.uniform(0.3, 0.5))
            net = revenue * (rng.uniform(0.25, 0.35) if bank else rng.uniform(0.08, 0.30))
            years.append({
                "year": this_year - i,
                "revenue": revenue,
                "gross_profit": None if bank else revenue * rng.uniform(0.35, 0.70),
                "net_income": net,
                "rnd": revenue * rng.uniform(0.10, 0.20) if tech else None,
                "total_debt": equity * (rng.uniform(1.0, 2.5) if bank else rng.uniform(0.2, 1.5)),
                "equity": equity,
                "total_assets": assets,
            })
            assets *= 1 + rng.uniform(0.0, 0.08)
        return build_fundamental_rows(years)


# --------------------------------------------------------------------------
# Yahoo provider
# --------------------------------------------------------------------------
class YahooProvider:
    name = "yahoo"

    def __init__(self):
        import yfinance  # imported lazily so tests don't need network

        self.yf = yfinance

    def info(self, ticker: str) -> dict:
        t = self.yf.Ticker(ticker)
        raw = t.info or {}
        if not raw or (raw.get("regularMarketPrice") is None and raw.get("currentPrice") is None and not raw.get("longName")):
            raise MarketDataError(f"No data found for '{ticker}'. Check the ticker symbol.")
        dy = _clean(raw.get("dividendYield"))
        # yfinance has reported dividendYield both as 0.025 and as 2.5; normalise.
        if dy is not None and dy > 1:
            dy = dy / 100
        return {
            "ticker": ticker,
            "name": raw.get("longName") or raw.get("shortName") or ticker,
            "sector": raw.get("sector") or "Unknown",
            "industry": raw.get("industry") or "Unknown",
            "currency": raw.get("currency") or "USD",
            "market_cap": _clean(raw.get("marketCap")),
            "pe": _clean(raw.get("trailingPE")),
            "pb": _clean(raw.get("priceToBook")),
            "dividend_yield": dy,
            "beta": _clean(raw.get("beta")),
        }

    def history(self, ticker: str, period: str) -> list[tuple[str, float]]:
        df = self.yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if df is None or df.empty:
            raise MarketDataError(f"No price history for '{ticker}'.")
        return [(idx.date().isoformat(), round(float(px), 4)) for idx, px in df["Close"].dropna().items()]

    @staticmethod
    def _row(df, *names):
        for n in names:
            if n in df.index:
                return df.loc[n]
        return None

    def fundamentals(self, ticker: str) -> list[dict]:
        t = self.yf.Ticker(ticker)
        inc, bal = t.income_stmt, t.balance_sheet
        if inc is None or inc.empty:
            raise MarketDataError(f"No financial statements for '{ticker}'.")
        rows = {
            "revenue": self._row(inc, "Total Revenue", "Operating Revenue"),
            "gross_profit": self._row(inc, "Gross Profit"),
            "net_income": self._row(inc, "Net Income", "Net Income Common Stockholders"),
            "rnd": self._row(inc, "Research And Development"),
        }
        if bal is not None and not bal.empty:
            rows["total_debt"] = self._row(bal, "Total Debt")
            rows["equity"] = self._row(bal, "Stockholders Equity", "Common Stock Equity")
            rows["total_assets"] = self._row(bal, "Total Assets")
        years = []
        for col in inc.columns:
            entry = {"year": col.year}
            for key, series in rows.items():
                entry[key] = series.get(col) if series is not None else None
            years.append(entry)
        return build_fundamental_rows(years)


# --------------------------------------------------------------------------
# Facade with caching + fallback
# --------------------------------------------------------------------------
class MarketData:
    def __init__(self, provider: str = "yahoo", fallback: bool = True, ttl: int = 900):
        self.fallback = fallback
        self.ttl = ttl
        self.demo = DemoProvider()
        self.primary = self.demo
        if provider == "yahoo":
            try:
                self.primary = YahooProvider()
            except ImportError:
                log.warning("yfinance not installed; using demo data")
        self._cache: dict = {}
        self._lock = threading.Lock()

    def _cached(self, key, fn):
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def _call(self, method: str, ticker: str, *args):
        def run():
            try:
                return {"source": self.primary.name, "data": getattr(self.primary, method)(ticker, *args)}
            except Exception as exc:  # network errors, rate limits, bad tickers
                if self.primary is self.demo or not self.fallback:
                    raise MarketDataError(str(exc)) from exc
                log.warning("Live %s failed for %s (%s); using demo data", method, ticker, exc)
                return {"source": "demo", "data": getattr(self.demo, method)(ticker, *args)}

        return self._cached((method, ticker, args), run)

    def info(self, ticker):
        return self._call("info", ticker)

    def history(self, ticker, period="1y"):
        return self._call("history", ticker, period)

    def fundamentals(self, ticker):
        return self._call("fundamentals", ticker)

    def latest_price(self, ticker):
        res = self.history(ticker, "1mo")
        if not res["data"]:
            raise MarketDataError(f"No recent price for '{ticker}'.")
        day, price = res["data"][-1]
        return {"source": res["source"], "date": day, "price": price}


# --------------------------------------------------------------------------
# Analytics used by the comparison page
# --------------------------------------------------------------------------
def performance_stats(series: list[tuple[str, float]]) -> dict:
    """Total return, annualised volatility, max drawdown for a price series."""
    prices = [p for _, p in series]
    if len(prices) < 2:
        return {"total_return": None, "annual_vol": None, "max_drawdown": None, "cagr": None}
    rets = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    peak, mdd = prices[0], 0.0
    for p in prices:
        peak = max(peak, p)
        mdd = min(mdd, p / peak - 1)
    years = len(prices) / 252
    total = prices[-1] / prices[0] - 1
    return {
        "total_return": total,
        "annual_vol": math.sqrt(var) * math.sqrt(252),
        "max_drawdown": mdd,
        "cagr": (1 + total) ** (1 / years) - 1 if years >= 1 else None,
    }


def correlation_matrix(series_by_ticker: dict[str, list[tuple[str, float]]]) -> dict:
    """Pearson correlation of daily returns on dates shared by all tickers."""
    tickers = list(series_by_ticker)
    maps = {t: dict(s) for t, s in series_by_ticker.items()}
    common = sorted(set.intersection(*(set(m) for m in maps.values()))) if maps else []
    rets = {}
    for t in tickers:
        px = [maps[t][d] for d in common]
        rets[t] = [px[i] / px[i - 1] - 1 for i in range(1, len(px))]

    def corr(a, b):
        n = len(a)
        if n < 3:
            return None
        ma, mb = sum(a) / n, sum(b) / n
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        sa = math.sqrt(sum((x - ma) ** 2 for x in a))
        sb = math.sqrt(sum((y - mb) ** 2 for y in b))
        return _safe_div(cov, sa * sb)

    return {"tickers": tickers, "matrix": [[corr(rets[a], rets[b]) for b in tickers] for a in tickers]}


def rebase(series: list[tuple[str, float]], base: float = 100.0) -> list[tuple[str, float]]:
    if not series:
        return []
    first = series[0][1]
    return [(d, round(p / first * base, 4)) for d, p in series]
