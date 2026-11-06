"""Client-discussion snapshots (Features 3–5).

* Save: one Save on the Compare page stores the whole comparison for a client,
  with the client's interests and the RM's notes. The page sends back the exact
  figures it showed, signed by the server (app/signing.py), so the saved record
  equals the screen even when another server handles the Save, and nothing is
  fetched again. A one-time submission ID stops a repeated request from saving twice.
* Search: by client, by company (ticker or name) and by date, using SQL on the
  discussions and discussion_companies tables (Amazon RDS on AWS).
* Reopen: shows the saved snapshot read-only. It never calls market data, so a
  historical discussion is never mixed with today's prices.
"""
import csv
import io
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from flask import Response, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from .api import parse_tickers, validate_period
from .auth import login_required
from .industry import TEMPLATES, template_payload
from .models import Client, Discussion, DiscussionCompany, db
from .signing import verify_text
from .routes import _create_client_from_form, _own_client, _parse_date, bp
from .timeutil import format_local, local_today

SNAPSHOT_VERSION = 1
SUBMISSION_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")
COMPARE_AGAIN = "Run Compare again, then save."


class SaveError(Exception):
    """A reason the discussion could not be saved, shown to the RM."""


def _own_discussion(discussion_id):
    d = db.session.get(Discussion, discussion_id)
    if d is None or d.rm_id != g.user.id:
        abort(404)
    return d


def _parse_utc(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _client_from_form(form):
    if form.get("client_id") == "__new__":
        client, error = _create_client_from_form(form, prefix="new_")
        if error:
            raise SaveError(error)
        return client
    try:
        client_id = int(form.get("client_id", ""))
    except (TypeError, ValueError):
        raise SaveError("Choose a client, or add a new one, before saving.") from None
    return _own_client(client_id)  # 404 if it is another RM's client


def _shown_figures(form, tickers, period):
    """The comparison and briefing exactly as the page showed them, after checking the server's signature."""
    texts = {}
    for part in ("compare", "briefing"):
        text, sig = form.get(f"{part}_json", ""), form.get(f"{part}_sig", "")
        if not verify_text(text, sig):
            raise SaveError("The figures on screen could not be verified, so nothing was saved. " + COMPARE_AGAIN)
        texts[part] = text
    comparison, briefing = json.loads(texts["compare"]), json.loads(texts["briefing"])
    if ([c.get("ticker") for c in comparison.get("companies", [])] != tickers
            or [c.get("ticker") for c in briefing.get("companies", [])] != tickers
            or comparison.get("period") != period):
        raise SaveError("The figures on screen are for different companies or a different period. " + COMPARE_AGAIN)
    return comparison, briefing


def build_discussion(form):
    """Validate the form and the figures shown, and return an unsaved Discussion."""
    try:
        tickers = parse_tickers(form.get("tickers", ""))
        period = validate_period(form.get("period", "1y"))
    except ValueError as exc:
        raise SaveError(str(exc)) from None
    limit = current_app.config["MAX_COMPARE_TICKERS"]
    if len(tickers) < 1:
        raise SaveError("Choose at least 1 company to save.")
    if len(tickers) > limit:
        raise SaveError(f"Save at most {limit} companies at once.")
    interests = form.get("client_interests", "").strip()
    if not interests:
        raise SaveError("Enter the client interests discussed; they are required for the record.")

    comparison, briefing = _shown_figures(form, tickers, period)
    client = _client_from_form(form)  # only after the figures are verified: no stray new client

    demo_mode = current_app.config["MARKET_DATA_PROVIDER"] == "demo"
    shown_demo = bool(comparison.get("demo_data") or briefing.get("demo_data"))
    if shown_demo and not demo_mode:
        raise SaveError("Live market data is not available right now (Yahoo Finance did not respond), "
                        "so this comparison was not saved: demo figures are never stored in a client "
                        "record. Try again in a few minutes.")
    data_source = "demo" if shown_demo else "yahoo"
    times = [t for t in (comparison.pop("retrieved_at", None), briefing.pop("retrieved_at", None)) if t]
    retrieved_at = min(times) if times else None
    trends = briefing.pop("trends", {}) or {}

    snapshot = {
        "version": SNAPSHOT_VERSION,
        "tickers": tickers,
        "period": period,
        "data_source": data_source,
        "retrieved_at": retrieved_at,
        "compare": comparison,
        "briefing": briefing,
        "trends": trends,
    }
    try:
        snapshot_json = json.dumps(snapshot, allow_nan=False, separators=(",", ":"))
    except ValueError:
        raise SaveError("The market data contained an invalid number, so nothing was saved.") from None

    discussion = Discussion(
        rm_id=g.user.id, client=client, period=period,
        discussed_on=_parse_date(form.get("discussed_on"), local_today()),
        follow_up_on=_parse_date(form.get("follow_up_on")),
        client_interests=interests[:5000],
        notes=form.get("notes", "").strip()[:10000] or None,
        data_source=data_source, data_retrieved_at=_parse_utc(retrieved_at),
        snapshot_json=snapshot_json,
    )
    for position, (shown, brief) in enumerate(zip(comparison["companies"], briefing["companies"])):
        fin = brief.get("financials") or {}
        if fin.get("error"):
            fin = {}
        last = shown["prices"][-1] if shown.get("prices") else None
        discussion.companies.append(DiscussionCompany(
            position=position, ticker=shown["ticker"], company_name=shown.get("name"),
            currency=shown.get("currency"),
            stock_price=Decimal(str(last[1])) if last else None,
            price_date=date.fromisoformat(last[0]) if last else None,
            price_source=data_source,
            fiscal_year=fin.get("year"), revenue_growth=fin.get("revenue_growth"),
            net_margin=fin.get("net_margin"), debt_to_equity=fin.get("debt_to_equity"),
        ))
    return discussion


def _already_saved(submission_id):
    return Discussion.query.filter_by(rm_id=g.user.id, submission_id=submission_id).first()


def _show_existing(existing):
    flash("This discussion was already saved; showing the saved record.", "info")
    return redirect(url_for("main.discussion_detail", discussion_id=existing.id))


@bp.route("/discussions", methods=["POST"])
@login_required
def discussion_save():
    submission_id = request.form.get("submission_id", "").strip()
    if not SUBMISSION_ID_RE.fullmatch(submission_id):
        submission_id = uuid.uuid4().hex  # no ID from the page: save, without duplicate protection
    existing = _already_saved(submission_id)
    if existing:  # the same click arrived again (e.g. a retried request)
        return _show_existing(existing)
    try:
        discussion = build_discussion(request.form)
    except SaveError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("main.compare", tickers=request.form.get("tickers", ""),
                                period=request.form.get("period", "1y")))
    discussion.submission_id = submission_id
    db.session.add(discussion)
    try:
        db.session.commit()
    except IntegrityError:
        # Two copies of the same request on two servers at the same moment: the other one won.
        db.session.rollback()
        existing = _already_saved(submission_id)
        if existing:
            return _show_existing(existing)
        flash("The discussion could not be saved (a client with that code was just created). " + COMPARE_AGAIN, "error")
        return redirect(url_for("main.compare", tickers=request.form.get("tickers", ""),
                                period=request.form.get("period", "1y")))
    flash(f"Saved the discussion with {discussion.client.label}. This is the record as saved; "
          "reopening it never fetches new data.", "success")
    return redirect(url_for("main.discussion_detail", discussion_id=discussion.id))


def _filtered_discussions(args):
    q = Discussion.query.filter(Discussion.rm_id == g.user.id).join(Client)
    if args.get("client_id"):
        q = q.filter(Discussion.client_id == args.get("client_id", type=int))
    term = (args.get("company") or "").strip()
    if term:
        matching = db.session.query(DiscussionCompany.discussion_id).filter(db.or_(
            DiscussionCompany.ticker == term.upper(),
            func.lower(DiscussionCompany.company_name).like(f"%{term.lower()}%"),
        ))
        q = q.filter(Discussion.id.in_(matching))
    if _parse_date(args.get("date_from")):
        q = q.filter(Discussion.discussed_on >= _parse_date(args["date_from"]))
    if _parse_date(args.get("date_to")):
        q = q.filter(Discussion.discussed_on <= _parse_date(args["date_to"]))
    if args.get("follow_up") == "due":
        q = q.filter(Discussion.follow_up_on.isnot(None), Discussion.follow_up_on <= local_today())
    elif args.get("follow_up") == "upcoming":
        q = q.filter(Discussion.follow_up_on > local_today())
    return q.order_by(Discussion.discussed_on.desc(), Discussion.id.desc())


def _csv_safe(value):
    """Stop spreadsheet apps treating free text such as notes as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@bp.route("/discussions/export.csv")
@login_required
def discussions_export():
    """Saved discussions as CSV, one row per company, with the same filters as the search."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Discussion date", "Client code", "Client name", "Ticker", "Company", "Currency",
                "Price at discussion", "Price date", "Price source", "Revenue growth", "Net margin",
                "Debt to equity", "Fiscal year", "Client interests", "Notes", "Follow up on",
                "Saved at (SGT)", "Link"])
    for d in _filtered_discussions(request.args).all():
        for c in d.companies:
            w.writerow([d.discussed_on, d.client.client_code, _csv_safe(d.client.name), c.ticker,
                        _csv_safe(c.company_name), c.currency,
                        c.stock_price if c.stock_price is not None else "", c.price_date or "",
                        c.price_source or "",
                        *["" if v is None else repr(v) for v in (c.revenue_growth, c.net_margin, c.debt_to_equity)],
                        c.fiscal_year or "", _csv_safe(d.client_interests), _csv_safe(d.notes),
                        d.follow_up_on or "", format_local(d.created_at, "%Y-%m-%d %H:%M").replace(" SGT", ""),
                        url_for("main.discussion_detail", discussion_id=d.id, _external=True)])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=nimbus_discussions.csv"})


@bp.route("/discussions")
@login_required
def discussions():
    rows = _filtered_discussions(request.args).limit(300).all()
    clients = Client.query.filter_by(rm_id=g.user.id).order_by(Client.client_code).all()
    return render_template("discussions.html", rows=rows, clients=clients, args=request.args)


@bp.route("/discussions/<int:discussion_id>")
@login_required
def discussion_detail(discussion_id):
    """Read-only: everything shown comes from the saved snapshot. No market data is requested."""
    d = _own_discussion(discussion_id)
    # Safe inside <script type="application/json">: "</" cannot close the tag.
    embedded = d.snapshot_json.replace("</", "<\\/")
    return render_template("discussion_detail.html", d=d, snapshot_json=embedded,
                           templates=TEMPLATES, template_payloads=[template_payload(k) for k in TEMPLATES])


@bp.route("/discussions/<int:discussion_id>/delete", methods=["POST"])
@login_required
def discussion_delete(discussion_id):
    d = _own_discussion(discussion_id)
    label = d.client.label
    db.session.delete(d)
    db.session.commit()
    flash(f"Deleted the discussion with {label}.", "success")
    return redirect(url_for("main.discussions"))
