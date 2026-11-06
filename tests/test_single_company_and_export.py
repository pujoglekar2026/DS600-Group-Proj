"""Changes from Jiang Minglan's review (7 Nov 2026):

1. One company on its own can be analysed and saved (not only comparisons of 2+).
2. Saved discussions are the main record: follow-ups, top tickers and the S3
   archive use them; stock notes stay as a secondary quick note.
3. Saved discussions can be exported to CSV.
"""
import csv
import io
import json
from datetime import date, datetime, timedelta

from app.models import Client, Discussion, DiscussionCompany, db

from .test_discussions import NoMarket, discussion_id, login, make_app, save, saved_snapshot


# ---- 1. one company -------------------------------------------------------------------
def test_one_company_can_be_analysed_saved_and_reopened():
    app = make_app()
    rm = login(app)
    d = rm.get("/api/compare?tickers=AAPL&period=1y").get_json()
    assert [c["ticker"] for c in d["companies"]] == ["AAPL"]
    did = discussion_id(save(rm, tickers="AAPL"))
    with app.app_context():
        assert [c.ticker for c in db.session.get(Discussion, did).companies] == ["AAPL"]
    app.extensions["market"] = NoMarket()
    page = rm.get(f"/discussions/{did}")
    assert page.status_code == 200 and saved_snapshot(page.data)["tickers"] == ["AAPL"]


def test_no_company_is_still_refused():
    app = make_app()
    rm = login(app)
    assert rm.get("/api/compare?tickers=&period=1y").status_code == 400
    r = save(rm, tickers="", payloads={})
    assert b"at least 1" in rm.get(r.headers["Location"]).data
    with app.app_context():
        assert Discussion.query.count() == 0


def test_page_explains_correlation_needs_two_companies():
    js = open("app/static/js/compare.js").read()
    assert "Correlation needs at least 2 companies" in js


# ---- 2. saved discussions are the main record ------------------------------------------
def test_dashboard_follow_ups_and_top_tickers_come_from_saved_discussions():
    app = make_app()
    rm = login(app)
    soon = (date.today() + timedelta(days=3)).isoformat()
    due = (date.today() - timedelta(days=1)).isoformat()
    a = discussion_id(save(rm, follow_up_on=soon))
    b = discussion_id(save(rm, client_id="1", tickers="AAPL,KO", follow_up_on=due))
    page = rm.get("/").data.decode()
    assert f'href="/discussions/{a}"' in page and f'href="/discussions/{b}"' in page  # follow-up list
    assert 'href="/discussions?follow_up=due"' in page
    assert ">1</div>" in page.split("Follow-ups due")[1][:80]  # one overdue
    assert 'href="/discussions?company=AAPL"' in page  # top tickers
    due_page = rm.get("/discussions?follow_up=due").data.decode()
    assert f"/discussions/{b}" in due_page and f"/discussions/{a}" not in due_page
    upcoming = rm.get("/discussions?follow_up=upcoming").data.decode()
    assert f"/discussions/{a}" in upcoming and f"/discussions/{b}" not in upcoming


def test_old_saved_discussions_are_archived_to_s3_and_verified():
    from app.archive import archive_discussions

    class FakeS3:
        def __init__(self):
            self.objects = {}

        def put_object(self, Bucket, Key, Body, **kw):
            self.objects[Key] = Body

        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(self.objects[Key])}

    app = make_app()
    rm = login(app)
    old = discussion_id(save(rm, discussed_on="2019-06-01"))
    recent = discussion_id(save(rm, client_id="1", discussed_on="2026-06-01"))
    s3 = FakeS3()
    with app.app_context():
        assert archive_discussions("bucket", s3, years=5, today=date(2026, 11, 7)) == 1
        assert db.session.get(Discussion, old) is None and db.session.get(Discussion, recent) is not None
        assert DiscussionCompany.query.filter_by(discussion_id=old).count() == 0
    (key, body), = s3.objects.items()
    # Under snapshots/ so the existing S3 lifecycle rule moves it to Glacier after 30 days.
    assert key.startswith(f"snapshots/discussions/{old}/")
    archived = json.loads(body)
    assert archived["client_code"] == "C-1001" and archived["discussed_on"] == "2019-06-01"
    assert [c["ticker"] for c in archived["companies"]] == ["AAPL", "MSFT"]
    assert json.loads(archived["snapshot_json"])["tickers"] == ["AAPL", "MSFT"]


def test_archive_keeps_the_discussion_if_s3_verification_fails():
    import pytest
    from app.archive import archive_discussions

    class BrokenS3:
        def put_object(self, **kw):
            pass

        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(b"corrupted")}

    app = make_app()
    rm = login(app)
    did = discussion_id(save(rm, discussed_on="2019-06-01"))
    with app.app_context():
        with pytest.raises(RuntimeError, match="verification failed"):
            archive_discussions("bucket", BrokenS3(), years=5, today=date(2026, 11, 7))
        assert db.session.get(Discussion, did) is not None


def test_stock_notes_are_described_as_secondary():
    app = make_app()
    page = login(app).get("/snapshots").data.decode()
    assert "Saved comparisons are under" in page


# ---- 3. CSV export -----------------------------------------------------------------------
def export_rows(client, query=""):
    r = client.get(f"/discussions/export.csv{query}")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"]
    return list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))


def test_discussions_export_one_row_per_company_with_saved_figures():
    app = make_app()
    rm = login(app)
    did = discussion_id(save(rm, notes="Prefers quarterly reviews."))
    rows = export_rows(rm)
    assert [r["Ticker"] for r in rows] == ["AAPL", "MSFT"]
    with app.app_context():
        d = db.session.get(Discussion, did)
        saved = {c.ticker: c for c in d.companies}
    for r in rows:
        c = saved[r["Ticker"]]
        assert r["Client code"] == "C-1001" and r["Discussion date"] == "2026-11-03"
        assert float(r["Price at discussion"]) == float(c.stock_price)
        assert float(r["Revenue growth"]) == c.revenue_growth and float(r["Debt to equity"]) == c.debt_to_equity
        assert r["Client interests"] == "Dividend income; worried about AI valuations"
        assert r["Price source"] == "yahoo" and r["Link"].endswith(f"/discussions/{did}")


def test_discussions_export_uses_the_search_filters_and_own_records_only():
    app = make_app()
    rm = login(app)
    save(rm, tickers="AAPL,MSFT")
    save(rm, client_id="1", tickers="KO,WMT", discussed_on="2026-01-15")
    assert {r["Ticker"] for r in export_rows(rm, "?company=apple")} == {"AAPL", "MSFT"}
    assert {r["Ticker"] for r in export_rows(rm, "?date_to=2026-06-01")} == {"KO", "WMT"}
    other = login(app, "rm2")
    assert export_rows(other) == []
    assert 'href="/discussions/export.csv?company=apple"' in rm.get("/discussions?company=apple").data.decode()


def test_export_neutralises_spreadsheet_formulas():
    app = make_app()
    rm = login(app)
    save(rm, client_interests="=HYPERLINK(\"http://evil\")", notes="+cmd")
    row = export_rows(rm)[0]
    assert row["Client interests"].startswith("'=") and row["Notes"].startswith("'+")


def test_compare_page_says_one_to_five():
    app = make_app()
    page = login(app).get("/compare").data.decode()
    assert "1–5 ticker" in page and "2–5" not in page
