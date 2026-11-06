"""JSON endpoints used by the comparison page's JavaScript."""
import re

from flask import Blueprint, current_app, g, jsonify, request

from .auth import login_required
from .industry import TEMPLATES, suggest_template, template_payload
from .market import MarketDataError, PERIODS, correlation_matrix, performance_stats, rebase
from .models import Client, Snapshot, db
from .signing import signed_json_response

bp = Blueprint("api", __name__)

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")


def market():
    return current_app.extensions["market"]


def parse_tickers(raw: str) -> list[str]:
    tickers = []
    for t in (raw or "").replace(" ", ",").split(","):
        t = t.strip().upper()
        if t and t not in tickers:
            if not TICKER_RE.match(t):
                raise ValueError(f"'{t}' is not a valid ticker symbol.")
            tickers.append(t)
    return tickers


def _require_tickers(min_n=1):
    tickers = parse_tickers(request.args.get("tickers", ""))
    limit = current_app.config["MAX_COMPARE_TICKERS"]
    if len(tickers) < min_n:
        raise ValueError(f"Enter at least {min_n} ticker{'s' if min_n > 1 else ''}.")
    if len(tickers) > limit:
        raise ValueError(f"Compare at most {limit} companies at once.")
    return tickers


@bp.errorhandler(ValueError)
@bp.errorhandler(MarketDataError)
def handle_bad_input(err):
    return jsonify({"error": str(err)}), 400


@bp.route("/health")
def health():
    """Used by the EC2 setup script (and a load balancer, if you add one)."""
    try:
        db.session.execute(db.text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({"status": "ok" if db_ok else "degraded", "database": db_ok}), (200 if db_ok else 503)


class Sources:
    """Collects where each piece of market data came from and when it was fetched."""

    def __init__(self):
        self.names, self.times = set(), []

    def add(self, result):
        self.names.add(result["source"])
        if result.get("fetched_at"):
            self.times.append(result["fetched_at"])
        return result

    @property
    def demo(self):
        return "demo" in self.names


def validate_period(period):
    if period not in PERIODS:
        raise ValueError(f"Period must be one of {', '.join(PERIODS)}.")
    return period


def comparison_payload(tickers, period, sources):
    """Price performance side by side (also used when a discussion is saved)."""
    companies, raw_series = [], {}
    for t in tickers:
        info = sources.add(market().info(t))
        hist = sources.add(market().history(t, period))
        series = hist["data"]
        raw_series[t] = series
        companies.append({
            **info["data"],
            "last_price": series[-1][1] if series else None,
            "stats": performance_stats(series),
            "rebased": rebase(series),
            "prices": series,
        })
    return {
        "period": period,
        "companies": companies,
        "correlation": correlation_matrix(raw_series),
        "suggested_template": suggest_template([c["sector"] for c in companies]),
        "demo_data": sources.demo,
    }


def _with_retrieved_at(payload, sources):
    payload["retrieved_at"] = min(sources.times) if sources.times else None
    return payload


@bp.route("/compare")
@login_required
def compare():
    tickers = _require_tickers(min_n=1)  # one company on its own is allowed (review, 7 Nov)
    period = validate_period(request.args.get("period", "1y"))
    sources = Sources()
    # Signed, so the Save form can send back exactly these figures (see app/signing.py).
    return signed_json_response(_with_retrieved_at(comparison_payload(tickers, period, sources), sources))


@bp.route("/fundamentals")
@login_required
def fundamentals():
    tickers = _require_tickers(min_n=1)
    key = request.args.get("template", "general")
    if key not in TEMPLATES:
        raise ValueError("Unknown template.")
    tpl = template_payload(key)
    out, sources = [], set()
    for t in tickers:
        info = market().info(t)
        fund = market().fundamentals(t)
        sources.update({info["source"], fund["source"]})
        out.append({
            "ticker": t,
            "name": info["data"]["name"],
            "currency": info["data"]["currency"],
            "snapshot": {m["key"]: info["data"].get(m["key"]) for m in tpl["metrics"] if m["source"] == "snapshot"},
            "trend": fund["data"],
        })
    return jsonify({"template": tpl, "companies": out, "demo_data": "demo" in sources})


FINANCIAL_KEYS = ("year", "revenue", "net_income", "total_debt", "equity",
                  "revenue_growth", "net_margin", "debt_to_equity")


def _latest_financials(ticker, sources, trends=None):
    """Latest fiscal year's statement lines plus the core ratios Gaurav asked for.

    If `trends` is a dict, every year's rows are kept in it too (for the saved snapshot).
    """
    try:
        fund = sources.add(market().fundamentals(ticker))
    except MarketDataError as exc:
        return {"error": str(exc)}
    rows = fund["data"] or []
    if trends is not None:
        trends[ticker] = rows
    if not rows:
        return {"error": "No financial statements available."}
    latest = rows[-1]
    return {**{k: latest.get(k) for k in FINANCIAL_KEYS}, "source": fund["source"]}


def _ratings(ticker, sources):
    try:
        res = sources.add(market().ratings(ticker))
    except MarketDataError as exc:
        return {"counts": None, "view": None, "message": str(exc), "source": None}
    data = res["data"]
    view = dict(data["view"]) if data.get("view") else None
    # Upside needs a live price; it is never computed from demo prices.
    if view and view.get("target_mean") and res["source"] != "demo":
        try:
            px = market().latest_price(ticker)
            if px["source"] != "demo" and px["price"]:
                view["upside"] = view["target_mean"] / px["price"] - 1
                view["price_used"] = px["price"]
        except MarketDataError:
            pass
    return {"counts": data.get("counts"), "view": view, "message": data.get("message"), "source": res["source"]}


def _news(ticker, name, sources):
    try:
        res = sources.add(market().news(ticker, name))
    except MarketDataError as exc:
        return {"items": [], "status": "error", "message": str(exc), "route": None, "source": None}
    return {**res["data"], "source": res["source"]}


def briefing_payload(tickers, sources, trends=None):
    """Feature 1: key financials, analyst ratings and sourced news for each company.

    Each part is fetched separately, so one failing part never hides the others.
    """
    companies = []
    for t in tickers:
        company = {"ticker": t, "name": t, "sector": None, "currency": None, "is_bank": False}
        try:
            info = sources.add(market().info(t))
            data = info["data"]
            company.update(name=data["name"], sector=data["sector"], currency=data["currency"],
                           is_bank=data["sector"] == "Financial Services")
        except MarketDataError as exc:
            company["error"] = str(exc)
        company["financials"] = _latest_financials(t, sources, trends)
        company["ratings"] = _ratings(t, sources)
        company["news"] = _news(t, company["name"], sources)
        companies.append(company)
    return {"companies": companies, "demo_data": sources.demo}


@bp.route("/briefing")
@login_required
def briefing():
    sources, trends = Sources(), {}
    payload = briefing_payload(_require_tickers(min_n=1), sources, trends)
    payload["trends"] = trends  # yearly rows, kept with a saved discussion for its trend charts
    return signed_json_response(_with_retrieved_at(payload, sources))


@bp.route("/templates")
@login_required
def templates():
    return jsonify([template_payload(k) for k in TEMPLATES])


@bp.route("/quote/<ticker>")
@login_required
def quote(ticker):
    t = parse_tickers(ticker)[0]
    info = market().info(t)
    px = market().latest_price(t)
    return jsonify({
        "ticker": t,
        "name": info["data"]["name"],
        "currency": info["data"]["currency"],
        "price": px["price"],
        "as_of": px["date"],
        "demo_data": "demo" in {info["source"], px["source"]},
    })


@bp.route("/snapshots")
@login_required
def snapshots():
    """Retrieve saved discussions, filtered by ?client=C-1001 and/or ?ticker=AAPL."""
    q = Snapshot.query.filter_by(rm_id=g.user.id).join(Client)
    if request.args.get("client"):
        q = q.filter(Client.client_code == request.args["client"].strip())
    if request.args.get("ticker"):
        q = q.filter(Snapshot.ticker == request.args["ticker"].strip().upper())
    return jsonify([s.to_dict() for s in q.order_by(Snapshot.discussed_on.desc()).limit(500)])
