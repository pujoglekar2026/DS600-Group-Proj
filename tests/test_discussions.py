"""Features 3–5: save a whole comparison as a client-discussion snapshot, search it
by client, company and date, and reopen it exactly as saved without fetching new data.

The first test is Gaurav's acceptance test (steps 1–11) as one scenario. No test
touches the network: market data comes from fakes.
"""
import json
import re
from datetime import date, timedelta

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from app import create_app, news
from app.config import TestConfig
from app.market import DemoProvider, MarketDataError
from app.models import Client, Discussion, DiscussionCompany, User, db

from .conftest import login_as
from .test_news import NEW_FORMAT, NO_SUMMARY


class LiveFake:
    """Looks like live Yahoo data (source "yahoo"); values come from the demo generator."""

    name = "yahoo"

    def __init__(self):
        self.demo = DemoProvider()

    def info(self, t):
        return self.demo.info(t)

    def history(self, t, period):
        return self.demo.history(t, period)

    def fundamentals(self, t):
        return self.demo.fundamentals(t)

    def news(self, t, name):
        items, skipped = news.parse_news([NEW_FORMAT, NO_SUMMARY])
        return {"items": items, "skipped": skipped, "status": "ok", "route": "Yahoo Finance news", "message": None}

    def ratings(self, t):
        return {"counts": {"strongBuy": 2, "buy": 5, "hold": 3, "sell": 0, "strongSell": 0, "period": "0m"},
                "view": {"recommendation": "buy", "target_mean": 500.0, "analysts": 10}, "message": None}


class NoMarket:
    """Replaces the market-data service: any use at all fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"market data was requested ({name}) while reopening a saved discussion")


def make_app(uri="sqlite:///:memory:", provider="demo", fallback=False, live=True):
    cfg = type("Cfg", (TestConfig,), {"SQLALCHEMY_DATABASE_URI": uri,
                                      "MARKET_DATA_PROVIDER": provider, "DEMO_FALLBACK": fallback})
    app = create_app(cfg)
    if live:
        app.extensions["market"].primary = LiveFake()
    return app


def login(app, username="rm1", create=True):
    if create:
        with app.app_context():
            if not User.query.filter_by(username=username).first():
                u = User(username=username, full_name=username.upper())
                u.set_password("password123")
                db.session.add(u)
                db.session.commit()
    c = app.test_client()
    c.post("/login", data={"username": username, "password": "password123"})
    return c


def shown_payloads(client, tickers="AAPL,MSFT", period="1y"):
    """What the Compare page receives: the two signed API responses, exactly as sent."""
    r1 = client.get("/api/compare", query_string={"tickers": tickers, "period": period})
    r2 = client.get("/api/briefing", query_string={"tickers": tickers})
    if r1.status_code != 200 or r2.status_code != 200:
        return {}
    return {"compare_json": r1.get_data(as_text=True), "compare_sig": r1.headers["X-Nimbus-Signature"],
            "briefing_json": r2.get_data(as_text=True), "briefing_sig": r2.headers["X-Nimbus-Signature"]}


def save(client, payloads=None, **overrides):
    """Submit the Save form as the Compare page does, with the figures it showed."""
    data = {"tickers": "AAPL,MSFT", "period": "1y", "client_id": "__new__",
            "new_client_code": "C-1001", "new_client_name": "Fictional Client One",
            "discussed_on": "2026-11-03", "client_interests": "Dividend income; worried about AI valuations",
            "notes": "Prefers quarterly reviews.", "follow_up_on": "2026-12-01"}
    data.update(overrides)
    if payloads is None:
        payloads = shown_payloads(client, data["tickers"], data["period"])
    data.update(payloads)
    return client.post("/discussions", data=data)


def discussion_id(response):
    m = re.search(r"/discussions/(\d+)$", response.headers.get("Location", ""))
    assert m, f"expected a redirect to the saved discussion, got {response.status_code} {response.headers.get('Location')}"
    return int(m.group(1))


def saved_snapshot(page_bytes):
    """The snapshot JSON embedded in the reopened page for the charts and tables."""
    m = re.search(rb'<script id="saved-discussion" type="application/json">(.*?)</script>', page_bytes, re.S)
    assert m, "reopened page must embed the saved snapshot"
    return json.loads(m.group(1))


# --------------------------------------------------------------------------
# Gaurav's acceptance test, steps 1–11
# --------------------------------------------------------------------------
def test_gaurav_acceptance_steps_1_to_11(tmp_path):
    uri = f"sqlite:///{tmp_path / 'nimbus.db'}"
    app = make_app(uri)
    rm = login(app)

    # 1–5: client ID and two tickers; generate; price performance side by side; growth, margin, D/E.
    compare = rm.get("/api/compare?tickers=AAPL,MSFT&period=1y").get_json()
    assert [c["ticker"] for c in compare["companies"]] == ["AAPL", "MSFT"]
    assert all(c["rebased"] and c["stats"]["total_return"] is not None for c in compare["companies"])
    briefing = rm.get("/api/briefing?tickers=AAPL,MSFT").get_json()
    for c in briefing["companies"]:
        assert {"revenue_growth", "net_margin", "debt_to_equity"} <= set(c["financials"])

    # 6–7: client interests, save.
    r = save(rm)
    did = discussion_id(r)
    with app.app_context():
        d = db.session.get(Discussion, did)
        assert d.client.client_code == "C-1001"
        assert d.client_interests.startswith("Dividend income")
        assert d.discussed_on == date(2026, 11, 3)
        rows = {c.ticker: c for c in d.companies}
        assert set(rows) == {"AAPL", "MSFT"}
        snap = json.loads(d.snapshot_json)
        for t, row in rows.items():
            shown = next(c for c in snap["compare"]["companies"] if c["ticker"] == t)
            fin = next(c for c in snap["briefing"]["companies"] if c["ticker"] == t)["financials"]
            assert float(row.stock_price) == pytest.approx(shown["last_price"])  # price at discussion = price shown
            assert row.price_date.isoformat() == shown["prices"][-1][0]
            assert row.price_source == "yahoo"
            assert row.revenue_growth == pytest.approx(fin["revenue_growth"])
            assert row.net_margin == pytest.approx(fin["net_margin"])
            assert row.debt_to_equity == pytest.approx(fin["debt_to_equity"])
        cid = d.client_id

    # 8: search by Client ID and reopen.
    listing = rm.get(f"/discussions?client_id={cid}").data
    assert f"/discussions/{did}".encode() in listing
    # 9: search by ticker, and by company name, and reopen.
    assert f"/discussions/{did}".encode() in rm.get("/discussions?company=msft").data
    assert f"/discussions/{did}".encode() in rm.get("/discussions?company=apple").data
    assert f"/discussions/{did}".encode() not in rm.get("/discussions?company=NVDA").data

    # 10: reopened values match the snapshot, and nothing is fetched to show them.
    app.extensions["market"] = NoMarket()
    page = rm.get(f"/discussions/{did}")
    assert page.status_code == 200
    assert b"Dividend income; worried about AI valuations" in page.data
    assert b"historical snapshot" in page.data.lower()
    assert saved_snapshot(page.data) == snap

    # 11: restart the app (new process, same database) and retrieve again.
    restarted = make_app(uri, live=False)
    restarted.extensions["market"] = NoMarket()
    rm2 = login(restarted, create=False)
    assert f"/discussions/{did}".encode() in rm2.get("/discussions?company=AAPL").data
    again = rm2.get(f"/discussions/{did}")
    assert again.status_code == 200 and saved_snapshot(again.data) == snap


# --------------------------------------------------------------------------
# Saving rules
# --------------------------------------------------------------------------
def test_existing_client_and_date_filters():
    app = make_app()
    rm = login(app)
    with app.app_context():
        c = Client(rm_id=1, client_code="C-2002", name="Existing Client")
        db.session.add(c)
        db.session.commit()
        cid = c.id
    old = discussion_id(save(rm, client_id=str(cid), discussed_on="2026-01-15"))
    new = discussion_id(save(rm, client_id=str(cid), discussed_on="2026-10-15", tickers="KO,WMT"))
    page = rm.get("/discussions?date_from=2026-06-01").data
    assert f"/discussions/{new}".encode() in page and f"/discussions/{old}".encode() not in page
    page = rm.get("/discussions?date_to=2026-06-01").data
    assert f"/discussions/{old}".encode() in page and f"/discussions/{new}".encode() not in page
    # Newest first, and the client page lists both.
    listing = rm.get("/discussions").data.decode()
    assert listing.index(f"/discussions/{new}") < listing.index(f"/discussions/{old}")
    client_page = rm.get(f"/clients/{cid}").data
    assert f"/discussions/{new}".encode() in client_page and f"/discussions/{old}".encode() in client_page


@pytest.mark.parametrize("overrides, message", [
    ({"tickers": ""}, b"at least 1"),
    ({"client_interests": "   "}, b"client interests"),
    ({"client_id": "", "new_client_code": ""}, b"client"),
    ({"tickers": "AAPL,<x>"}, b"not a valid ticker"),
    ({"period": "9y"}, b"period"),
])
def test_invalid_input_is_refused_and_nothing_saved(overrides, message):
    app = make_app()
    rm = login(app)
    r = save(rm, **overrides)
    assert r.status_code in (302, 400)
    follow = rm.get(r.headers["Location"]) if r.status_code == 302 else r
    assert message in follow.data.lower()
    with app.app_context():
        assert Discussion.query.count() == 0 and DiscussionCompany.query.count() == 0


def test_demo_fallback_data_is_never_saved_as_a_client_record():
    class YahooDown(LiveFake):
        def _fail(self, *a, **k):
            raise MarketDataError("simulated Yahoo rate limit")
        info = history = fundamentals = ratings = _fail

        def news(self, t, name):
            raise MarketDataError("simulated Yahoo rate limit")

    app = make_app(provider="yahoo", fallback=True)
    app.extensions["market"].primary = YahooDown()
    rm = login(app)
    r = save(rm)
    follow = rm.get(r.headers["Location"]) if r.status_code == 302 else r
    assert b"live market data" in follow.data.lower()
    with app.app_context():
        assert Discussion.query.count() == 0


def test_explicit_demo_mode_saves_and_labels_it():
    app = make_app(provider="demo", live=False)
    rm = login(app)
    did = discussion_id(save(rm))
    with app.app_context():
        d = db.session.get(Discussion, did)
        assert d.data_source == "demo"
        assert {c.price_source for c in d.companies} == {"demo"}
    assert b"Demo data" in rm.get(f"/discussions/{did}").data
    assert b"Demo data" in rm.get("/discussions").data


def test_rm_cannot_see_or_use_another_rms_discussions():
    app = make_app()
    rm1 = login(app, "rm1")
    did = discussion_id(save(rm1))
    with app.app_context():
        cid = db.session.get(Discussion, did).client_id
    rm2 = login(app, "rm2")
    assert rm2.get(f"/discussions/{did}").status_code == 404
    assert f"/discussions/{did}".encode() not in rm2.get("/discussions").data
    # rm2 cannot attach a discussion to rm1's client.
    r = save(rm2, client_id=str(cid))
    assert r.status_code == 404
    with app.app_context():
        assert Discussion.query.count() == 1


def test_deleting_a_client_deletes_their_discussions():
    app = make_app()
    rm = login(app)
    did = discussion_id(save(rm))
    with app.app_context():
        cid = db.session.get(Discussion, did).client_id
    rm.post(f"/clients/{cid}/delete")
    with app.app_context():
        assert Discussion.query.count() == 0 and DiscussionCompany.query.count() == 0


def test_delete_discussion():
    app = make_app()
    rm = login(app)
    did = discussion_id(save(rm))
    assert rm.post(f"/discussions/{did}/delete").status_code == 302
    with app.app_context():
        assert Discussion.query.count() == 0 and DiscussionCompany.query.count() == 0


# --------------------------------------------------------------------------
# Storage and page details
# --------------------------------------------------------------------------
def test_snapshot_column_holds_large_snapshots_on_mysql():
    """A 5-year comparison of 5 companies is several hundred KB; MySQL TEXT stops at 64 KB."""
    ddl = str(CreateTable(Discussion.__table__).compile(dialect=mysql.dialect()))
    assert "snapshot_json MEDIUMTEXT" in ddl


def test_ratio_columns_keep_full_precision_on_mysql():
    """MySQL FLOAT keeps ~7 digits; saved ratios must equal the snapshot exactly."""
    ddl = str(CreateTable(DiscussionCompany.__table__).compile(dialect=mysql.dialect()))
    for col in ("revenue_growth", "net_margin", "debt_to_equity"):
        assert f"{col} DOUBLE" in ddl


def test_five_year_five_company_snapshot_saves():
    app = make_app()
    rm = login(app)
    did = discussion_id(save(rm, tickers="AAPL,MSFT,NVDA,KO,WMT", period="5y"))
    with app.app_context():
        assert len(db.session.get(Discussion, did).companies) == 5


def test_reopen_page_runs_the_shared_charts_in_saved_mode():
    app = make_app()
    rm = login(app)
    did = discussion_id(save(rm))
    page = rm.get(f"/discussions/{did}").data
    assert b'id="price-chart"' in page and b'id="news-list"' in page
    assert b'id="compare-form"' not in page  # nothing to re-run
    js = open("app/static/js/compare.js").read()
    assert "saved-discussion" in js


def test_navigation_and_pages_render():
    app = make_app()
    rm = login(app)
    did = discussion_id(save(rm))
    nav = rm.get("/compare").data
    assert b'href="/discussions"' in nav and b">Discussions</a>" in nav and b">Stock notes</a>" in nav
    assert b'action="/discussions"' in nav  # one Save for the whole comparison
    for url in ["/discussions", f"/discussions/{did}", "/discussions?company=apple&client_id=1"]:
        assert rm.get(url).status_code == 200, url


def test_clients_list_counts_discussions_and_last_spoke():
    """Found in the 4 Nov QA run (and again on 7 Nov): the Clients list must count saved discussions."""
    app = make_app()
    rm = login(app)
    save(rm, discussed_on="2026-10-20")
    page = rm.get("/clients").data.decode()
    row = next(r for r in re.findall(r"<tr class=\"clickable\".*?</tr>", page, re.S) if "C-1001" in r)
    cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    assert cells[0] == "C-1001"
    assert cells[4:7] == ["1", "0", "20 Oct 2026"]  # discussions, stock notes, last spoke


def test_dates_and_times_follow_singapore_time(monkeypatch):
    """Found on AWS (4 Nov): servers run on UTC, so at 03:47 SGT the app said 3 Nov."""
    from datetime import datetime, timezone
    from app import timeutil

    frozen = datetime(2026, 11, 3, 19, 47, tzinfo=timezone.utc)  # 04 Nov 2026, 03:47 SGT
    monkeypatch.setattr(timeutil, "_utcnow", lambda: frozen)
    app = make_app()
    rm = login(app)
    assert b'value="2026-11-04"' in rm.get("/compare").data  # Save form's default date
    did = discussion_id(save(rm, discussed_on=""))  # date left empty -> today in SGT
    with app.app_context():
        d = db.session.get(Discussion, did)
        assert d.discussed_on == date(2026, 11, 4)
        d.created_at = frozen.replace(tzinfo=None)  # stored as UTC
        db.session.commit()
    page = rm.get(f"/discussions/{did}").data
    assert b"04 Nov 2026, 03:47 SGT" in page and b"UTC" not in page



# --------------------------------------------------------------------------
# Several servers behind the load balancer (found on AWS, 5 Nov 2026)
# --------------------------------------------------------------------------
class OtherServerFake(LiveFake):
    """A second server whose cache holds slightly different prices (fetched at another moment)."""

    def history(self, t, period):
        return [(d, round(p * 1.0137, 4)) for d, p in super().history(t, period)]


def two_servers(tmp_path):
    uri = f"sqlite:///{tmp_path / 'shared.db'}"
    a = make_app(uri)
    b = make_app(uri, live=False)
    b.extensions["market"].primary = OtherServerFake()
    return a, login(a), b, login(b)


def test_save_stores_exactly_what_was_shown_even_when_another_server_saves(tmp_path):
    """On AWS the Compare page came from one server and the Save went to another, which fetched
    fresh prices: AAPL showed 351.26 but 351.74 was saved."""
    a, rm_a, b, rm_b = two_servers(tmp_path)
    shown = shown_payloads(rm_a)
    shown_prices = {c["ticker"]: c["last_price"] for c in json.loads(shown["compare_json"])["companies"]}
    other_prices = {c["ticker"]: c["last_price"]
                    for c in rm_b.get("/api/compare?tickers=AAPL,MSFT&period=1y").get_json()["companies"]}
    assert shown_prices != other_prices
    did = discussion_id(save(rm_b, payloads=shown))
    with b.app_context():
        saved = {c.ticker: float(c.stock_price) for c in db.session.get(Discussion, did).companies}
    assert saved == pytest.approx(shown_prices)


def test_saving_makes_no_market_data_calls():
    app = make_app()
    rm = login(app)
    shown = shown_payloads(rm)
    app.extensions["market"] = NoMarket()
    did = discussion_id(save(rm, payloads=shown))
    with app.app_context():
        assert db.session.get(Discussion, did) is not None


@pytest.mark.parametrize("tamper", [
    lambda p: {**p, "compare_json": p["compare_json"].replace('"last_price":', '"last_price":1', 1)},
    lambda p: {**p, "briefing_sig": "0" * 64},
    lambda p: {k: v for k, v in p.items() if not k.startswith("compare")},
    lambda p: {},
])
def test_unverified_or_missing_figures_are_refused(tamper):
    app = make_app()
    rm = login(app)
    r = save(rm, payloads=tamper(shown_payloads(rm)))
    assert b"compare again" in rm.get(r.headers["Location"]).data.lower()
    with app.app_context():
        assert Discussion.query.count() == 0 and Client.query.count() == 0


def test_figures_for_other_companies_are_refused():
    app = make_app()
    rm = login(app)
    r = save(rm, payloads=shown_payloads(rm, "KO,WMT"))  # form says AAPL,MSFT
    assert b"compare again" in rm.get(r.headers["Location"]).data.lower()
    with app.app_context():
        assert Discussion.query.count() == 0


def test_one_click_is_saved_once_even_if_the_request_arrives_twice():
    """On AWS one click produced two identical discussions (a repeated request)."""
    app = make_app()
    rm = login(app)
    shown = shown_payloads(rm)
    first = discussion_id(save(rm, payloads=shown, submission_id="7f9c2b1e-0d4a-4c55-9a1e-3b8f6d2c1a00"))
    second = save(rm, payloads=shown, submission_id="7f9c2b1e-0d4a-4c55-9a1e-3b8f6d2c1a00")
    assert discussion_id(second) == first
    assert b"already saved" in rm.get(second.headers["Location"]).data
    with app.app_context():
        assert Discussion.query.count() == 1 and Client.query.count() == 1
    # A new Compare run gets a new submission ID and saves a second discussion.
    other = discussion_id(save(rm, payloads=shown, client_id="1", submission_id="0b3d5e7f-1111-4222-8333-944455556666"))
    assert other != first


def test_submission_ids_are_unique_per_rm_on_mysql():
    ddl = str(CreateTable(Discussion.__table__).compile(dialect=mysql.dialect()))
    assert "UNIQUE (rm_id, submission_id)" in ddl


def test_compare_page_sends_what_it_showed():
    js = open("app/static/js/compare.js").read()
    page_fields = ("compare_json", "compare_sig", "briefing_json", "briefing_sig", "submission_id")
    app = make_app()
    page = login(app).get("/compare").data.decode()
    for field in page_fields:
        assert f'name="{field}"' in page, field
    assert "X-Nimbus-Signature" in js and "randomUUID" in js


def test_large_comparisons_fit_the_form_limit():
    from app.config import Config
    assert Config.MAX_FORM_MEMORY_SIZE >= 2 * 1024 * 1024
    nginx = open("deploy/nginx-cloudfront.conf").read()
    assert "client_max_body_size 4m;" in nginx


def test_simultaneous_copies_of_one_click_are_saved_once(monkeypatch):
    """Both copies pass the 'already saved?' check at the same moment; the database's
    unique (rm_id, submission_id) rule stops the second, which then shows the first."""
    from app import discussions

    app = make_app()
    rm = login(app)
    shown = shown_payloads(rm)
    sid = "3c1e9a77-5b2d-4f0e-8a64-0d9b7e215c42"
    first = discussion_id(save(rm, payloads=shown, submission_id=sid))
    real = discussions._already_saved
    calls = []

    def blind_first_check(submission_id):  # the second copy's first check ran before the first commit
        calls.append(submission_id)
        return None if len(calls) == 1 else real(submission_id)

    monkeypatch.setattr(discussions, "_already_saved", blind_first_check)
    second = save(rm, payloads=shown, submission_id=sid, client_id="1")
    assert discussion_id(second) == first
    with app.app_context():
        assert Discussion.query.count() == 1


def test_prices_round_the_same_on_server_pages_as_in_the_browser():
    """AWS fix run (5 Nov): 353.905 showed as 353.91 on Compare but 353.90 in the saved record."""
    from decimal import Decimal
    app = make_app()
    money = app.jinja_env.filters["money"]
    # Expected values are what the page's JavaScript (toLocaleString) shows.
    assert [money(v) for v in (353.905, Decimal("353.9050"), 1.005, 2.675, 1234.565, 353.9049)] == \
        ["353.91", "353.91", "1.01", "2.68", "1,234.57", "353.90"]
    assert money(None) == "—"


def test_save_button_recovers_after_browser_back():
    js = open("app/static/js/compare.js").read()
    assert "addEventListener('pageshow'" in js


def test_dashboard_counts_saved_discussions():
    app = make_app()
    rm = login(app)
    save(rm, discussed_on=date.today().isoformat())
    page = rm.get("/").data.decode()
    tile = re.search(r'Discussions saved</div><div class="tile-value">(\d+)', page).group(1)
    month = re.search(r'This month</div><div class="tile-value">(\d+)', page).group(1)
    assert (tile, month) == ("1", "1")
    assert 'href="/discussions"' in page



def test_menu_discussions_finds_a_saved_comparison_by_client_and_company():
    """Gaurav's steps 8–9 as an RM clicks them: menu "Discussions" -> search -> the saved comparison.
    On 7 Nov the menu item pointed at the one-stock notes page and showed "No discussions match"."""
    app = make_app()
    rm = login(app)
    did = discussion_id(save(rm, new_client_code="C-AWS-01"))
    nav = rm.get("/").data.decode()
    menu = re.search(r"<nav.*?</nav>", nav, re.S).group(0)
    target = re.search(r'href="([^"]+)"[^>]*>Discussions</a>', menu).group(1)
    with app.app_context():
        cid = db.session.get(Discussion, did).client_id
    assert f"/discussions/{did}".encode() in rm.get(f"{target}?client_id={cid}").data
    assert f"/discussions/{did}".encode() in rm.get(f"{target}?company=MSFT").data
