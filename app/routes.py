"""HTML pages: dashboard, comparison, clients and discussion snapshots."""
import csv
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)
from sqlalchemy import func

from .api import parse_tickers
from .auth import login_required
from .industry import TEMPLATES
from .market import PERIODS, MarketDataError
from .models import Client, Snapshot, db

bp = Blueprint("main", __name__)


def market():
    return current_app.extensions["market"]


def _parse_date(value, default=None):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def _own_client(client_id):
    client = db.session.get(Client, client_id)
    if client is None or client.rm_id != g.user.id:
        abort(404)
    return client


def _own_snapshot(snapshot_id):
    snap = db.session.get(Snapshot, snapshot_id)
    if snap is None or snap.rm_id != g.user.id:
        abort(404)
    return snap


def _latest_prices(tickers):
    """Current price per ticker; missing if the market data call fails."""
    out = {}
    for t in set(tickers):
        try:
            out[t] = market().latest_price(t)["price"]
        except MarketDataError:
            pass
    return out


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    today = date.today()
    base = Snapshot.query.filter_by(rm_id=g.user.id)
    follow_ups = (base.filter(Snapshot.follow_up_on.isnot(None), Snapshot.follow_up_on <= today + timedelta(days=14))
                  .order_by(Snapshot.follow_up_on).limit(10).all())
    recent = base.order_by(Snapshot.discussed_on.desc(), Snapshot.id.desc()).limit(6).all()
    top_tickers = (db.session.query(Snapshot.ticker, func.count(Snapshot.id))
                   .filter(Snapshot.rm_id == g.user.id).group_by(Snapshot.ticker)
                   .order_by(func.count(Snapshot.id).desc()).limit(5).all())
    stats = {
        "clients": Client.query.filter_by(rm_id=g.user.id).count(),
        "snapshots": base.count(),
        "this_month": base.filter(Snapshot.discussed_on >= today.replace(day=1)).count(),
        "due": base.filter(Snapshot.follow_up_on.isnot(None), Snapshot.follow_up_on <= today).count(),
    }
    return render_template("dashboard.html", stats=stats, follow_ups=follow_ups,
                           recent=recent, top_tickers=top_tickers, today=today)


# --------------------------------------------------------------------------
# Comparison (the page itself; data comes from /api/compare + /api/fundamentals)
# --------------------------------------------------------------------------
@bp.route("/compare")
@login_required
def compare():
    return render_template("compare.html", periods=list(PERIODS), templates=TEMPLATES,
                           initial=request.args.get("tickers", ""),
                           max_tickers=current_app.config["MAX_COMPARE_TICKERS"])


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
@bp.route("/clients")
@login_required
def clients():
    rows = (db.session.query(Client, func.count(Snapshot.id), func.max(Snapshot.discussed_on))
            .outerjoin(Snapshot).filter(Client.rm_id == g.user.id)
            .group_by(Client.id).order_by(Client.client_code).all())
    return render_template("clients.html", rows=rows)


@bp.route("/clients/new", methods=["GET", "POST"])
@login_required
def client_new():
    if request.method == "POST":
        client, error = _create_client_from_form(request.form)
        if error:
            flash(error, "error")
        else:
            db.session.commit()
            flash(f"Client {client.label} added.", "success")
            return redirect(url_for("main.client_detail", client_id=client.id))
    return render_template("client_form.html")


def _create_client_from_form(form, prefix=""):
    code = form.get(prefix + "client_code", "").strip().upper()
    name = form.get(prefix + "client_name", "").strip()
    if not code or not name:
        return None, "Client code and client name are both required."
    if Client.query.filter_by(rm_id=g.user.id, client_code=code).first():
        return None, f"You already have a client with code {code}."
    client = Client(rm_id=g.user.id, client_code=code, name=name,
                    segment=form.get(prefix + "segment") or None,
                    risk_profile=form.get(prefix + "risk_profile") or None)
    db.session.add(client)
    db.session.flush()
    return client, None


@bp.route("/clients/<int:client_id>")
@login_required
def client_detail(client_id):
    client = _own_client(client_id)
    prices = _latest_prices([s.ticker for s in client.snapshots])
    return render_template("client_detail.html", client=client, prices=prices, today=date.today())


@bp.route("/clients/<int:client_id>/delete", methods=["POST"])
@login_required
def client_delete(client_id):
    client = _own_client(client_id)
    db.session.delete(client)
    db.session.commit()
    flash(f"Deleted client {client.label} and their records.", "success")
    return redirect(url_for("main.clients"))


# --------------------------------------------------------------------------
# Discussion snapshots
# --------------------------------------------------------------------------
def _filtered_snapshots():
    args = request.args
    q = Snapshot.query.filter(Snapshot.rm_id == g.user.id).join(Client)
    if args.get("client_id"):
        q = q.filter(Snapshot.client_id == args.get("client_id", type=int))
    if args.get("ticker"):
        q = q.filter(Snapshot.ticker == args["ticker"].strip().upper())
    if args.get("interest") in Snapshot.INTEREST_LEVELS:
        q = q.filter(Snapshot.interest_level == args["interest"])
    if _parse_date(args.get("date_from")):
        q = q.filter(Snapshot.discussed_on >= _parse_date(args["date_from"]))
    if _parse_date(args.get("date_to")):
        q = q.filter(Snapshot.discussed_on <= _parse_date(args["date_to"]))
    if args.get("q"):
        like = f"%{args['q'].strip()}%"
        q = q.filter(db.or_(Snapshot.notes.ilike(like), Snapshot.tags.ilike(like),
                            Client.name.ilike(like), Snapshot.company_name.ilike(like)))
    return q.order_by(Snapshot.discussed_on.desc(), Snapshot.id.desc())


@bp.route("/snapshots")
@login_required
def snapshots():
    rows = _filtered_snapshots().limit(300).all()
    prices = _latest_prices([s.ticker for s in rows]) if request.args.get("live") else {}
    clients = Client.query.filter_by(rm_id=g.user.id).order_by(Client.client_code).all()
    tickers = [t for (t,) in db.session.query(Snapshot.ticker).filter_by(rm_id=g.user.id).distinct().order_by(Snapshot.ticker)]
    return render_template("snapshots.html", rows=rows, clients=clients, tickers=tickers,
                           prices=prices, levels=Snapshot.INTEREST_LEVELS, args=request.args)


@bp.route("/snapshots/export.csv")
@login_required
def snapshots_export():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Discussed on", "Client code", "Client name", "Ticker", "Company", "Price",
                     "Currency", "Interest", "Tags", "Notes", "Follow up on"])
    for s in _filtered_snapshots().all():
        writer.writerow([s.discussed_on, s.client.client_code, s.client.name, s.ticker, s.company_name,
                         s.price_at_discussion, s.currency, s.interest_level, s.tags or "", s.notes or "",
                         s.follow_up_on or ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=nimbus_snapshots.csv"})


def _fill_snapshot(snap, form):
    """Validate the form and copy it onto `snap`. Returns an error message or None."""
    try:
        ticker = parse_tickers(form.get("ticker", ""))[0]
    except (ValueError, IndexError):
        return "Enter a valid ticker symbol."

    # Client: an existing one, or a new one typed into the form.
    if form.get("client_id") == "__new__":
        client, error = _create_client_from_form(form, prefix="new_")
        if error:
            return error
    else:
        try:
            client = _own_client(int(form.get("client_id", "0")))
        except (ValueError, TypeError):
            return "Choose a client."

    # Price: what the RM typed, otherwise the latest market price.
    price_raw = form.get("price", "").strip()
    company, currency = None, "USD"
    try:
        info = market().info(ticker)["data"]
        company, currency = info["name"], info["currency"]
    except MarketDataError:
        pass
    if price_raw:
        try:
            price = Decimal(price_raw)
            if price <= 0:
                raise InvalidOperation
        except InvalidOperation:
            return "Price must be a positive number."
    else:
        try:
            price = Decimal(str(market().latest_price(ticker)["price"]))
        except MarketDataError as exc:
            return f"Couldn't fetch a price for {ticker} ({exc}). Type the price in manually."

    level = form.get("interest_level", "Medium")
    snap.client = client
    snap.rm_id = g.user.id
    snap.ticker = ticker
    snap.company_name = company or snap.company_name or ticker
    snap.currency = currency
    snap.price_at_discussion = price
    snap.discussed_on = _parse_date(form.get("discussed_on"), date.today())
    snap.interest_level = level if level in Snapshot.INTEREST_LEVELS else "Medium"
    snap.tags = ", ".join(t.strip() for t in form.get("tags", "").split(",") if t.strip())[:255] or None
    snap.notes = form.get("notes", "").strip() or None
    snap.follow_up_on = _parse_date(form.get("follow_up_on"))
    return None


@bp.route("/snapshots/new", methods=["GET", "POST"])
@login_required
def snapshot_new():
    clients = Client.query.filter_by(rm_id=g.user.id).order_by(Client.client_code).all()
    if request.method == "POST":
        snap = Snapshot()
        error = _fill_snapshot(snap, request.form)
        if error:
            db.session.rollback()
            flash(error, "error")
            return render_template("snapshot_form.html", snap=None, form=request.form, clients=clients,
                                   levels=Snapshot.INTEREST_LEVELS, today=date.today())
        db.session.add(snap)
        db.session.commit()
        flash(f"Saved discussion with {snap.client.label} about {snap.ticker}.", "success")
        return redirect(url_for("main.client_detail", client_id=snap.client_id))
    prefill = {"ticker": request.args.get("ticker", ""), "price": request.args.get("price", ""),
               "client_id": request.args.get("client_id", "")}
    return render_template("snapshot_form.html", snap=None, form=prefill, clients=clients,
                           levels=Snapshot.INTEREST_LEVELS, today=date.today())


@bp.route("/snapshots/<int:snapshot_id>/edit", methods=["GET", "POST"])
@login_required
def snapshot_edit(snapshot_id):
    snap = _own_snapshot(snapshot_id)
    clients = Client.query.filter_by(rm_id=g.user.id).order_by(Client.client_code).all()
    if request.method == "POST":
        error = _fill_snapshot(snap, request.form)
        if error:
            db.session.rollback()
            flash(error, "error")
        else:
            db.session.commit()
            flash("Discussion updated.", "success")
            return redirect(url_for("main.client_detail", client_id=snap.client_id))
    form = {
        "ticker": snap.ticker, "price": snap.price_at_discussion, "client_id": str(snap.client_id),
        "discussed_on": snap.discussed_on.isoformat(), "interest_level": snap.interest_level,
        "tags": snap.tags or "", "notes": snap.notes or "",
        "follow_up_on": snap.follow_up_on.isoformat() if snap.follow_up_on else "",
    }
    return render_template("snapshot_form.html", snap=snap, form=form, clients=clients,
                           levels=Snapshot.INTEREST_LEVELS, today=date.today())


@bp.route("/snapshots/<int:snapshot_id>/delete", methods=["POST"])
@login_required
def snapshot_delete(snapshot_id):
    snap = _own_snapshot(snapshot_id)
    client_id = snap.client_id
    db.session.delete(snap)
    db.session.commit()
    flash("Discussion deleted.", "success")
    return redirect(request.form.get("next") or url_for("main.client_detail", client_id=client_id))
