"""JSON endpoints used by the comparison page's JavaScript."""
import re

from flask import Blueprint, current_app, g, jsonify, request

from .auth import login_required
from .industry import TEMPLATES, suggest_template, template_payload
from .market import MarketDataError, PERIODS, correlation_matrix, performance_stats, rebase
from .models import Client, Snapshot, db

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


@bp.route("/compare")
@login_required
def compare():
    tickers = _require_tickers(min_n=2)
    period = request.args.get("period", "1y")
    if period not in PERIODS:
        raise ValueError(f"Period must be one of {', '.join(PERIODS)}.")

    companies, raw_series, sources = [], {}, set()
    for t in tickers:
        info = market().info(t)
        hist = market().history(t, period)
        sources.update({info["source"], hist["source"]})
        series = hist["data"]
        raw_series[t] = series
        companies.append({
            **info["data"],
            "last_price": series[-1][1] if series else None,
            "stats": performance_stats(series),
            "rebased": rebase(series),
            "prices": series,
        })

    return jsonify({
        "period": period,
        "companies": companies,
        "correlation": correlation_matrix(raw_series),
        "suggested_template": suggest_template([c["sector"] for c in companies]),
        "demo_data": "demo" in sources,
    })


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
