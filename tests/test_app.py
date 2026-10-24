from app.models import Client, Snapshot, db

from .conftest import login_as


def test_pages_require_login(client):
    assert client.get("/").status_code == 302
    assert client.get("/api/compare?tickers=AAPL,MSFT").status_code == 401


def test_bad_login(client):
    r = client.post("/login", data={"username": "rm1", "password": "wrong"})
    assert b"Incorrect username or password" in r.data


def test_register(client):
    r = client.post("/register", data={"username": "newrm", "full_name": "New RM", "password": "longenough"})
    assert r.status_code == 302
    assert client.get("/").status_code == 200


def test_health(client):
    assert client.get("/api/health").get_json()["database"] is True


def test_compare_api(logged_in):
    d = logged_in.get("/api/compare?tickers=JPM,BAC&period=6mo").get_json()
    assert [c["ticker"] for c in d["companies"]] == ["JPM", "BAC"]
    assert [c["name"] for c in d["companies"]] == ["JPMorgan Chase & Co.", "Bank of America Corporation"]
    assert d["suggested_template"] == "banking"
    assert d["companies"][0]["rebased"][0][1] == 100.0
    assert len(d["correlation"]["matrix"]) == 2


def test_compare_validation(logged_in):
    assert logged_in.get("/api/compare?tickers=AAPL").status_code == 400
    assert logged_in.get("/api/compare?tickers=A,B,C,D,E,F").status_code == 400
    assert logged_in.get("/api/compare?tickers=AAPL,MSFT&period=99y").status_code == 400
    assert logged_in.get("/api/compare?tickers=AAPL,BAD%3B--").status_code == 400


def test_fundamentals_api(logged_in):
    d = logged_in.get("/api/fundamentals?tickers=AAPL,MSFT&template=technology").get_json()
    assert d["template"]["key"] == "technology"
    assert "pe" in d["companies"][0]["snapshot"]
    assert d["companies"][0]["trend"][-1]["rnd_intensity"] is not None


def test_create_snapshot_with_new_client_and_auto_price(logged_in, app):
    r = logged_in.post("/snapshots/new", data={
        "client_id": "__new__", "new_client_code": "c-1", "new_client_name": "Alice Tan",
        "ticker": "d05.si", "price": "", "interest_level": "High",
        "tags": "dividends,  SG banks ,", "notes": "Wants income", "follow_up_on": "2030-01-01",
    })
    assert r.status_code == 302
    with app.app_context():
        s = Snapshot.query.one()
        assert s.ticker == "D05.SI" and s.client.client_code == "C-1"
        assert float(s.price_at_discussion) > 0
        assert s.tag_list == ["dividends", "SG banks"]
        assert s.currency == "SGD"


def test_retrieve_by_client_and_ticker(logged_in, app):
    with app.app_context():
        c = Client(rm_id=1, client_code="C-9", name="Bob")
        db.session.add(c)
        db.session.commit()
        cid = c.id
    for t in ["AAPL", "MSFT"]:
        logged_in.post("/snapshots/new", data={"client_id": str(cid), "ticker": t, "price": "100"})
    assert len(logged_in.get("/api/snapshots?client=C-9").get_json()) == 2
    only = logged_in.get("/api/snapshots?ticker=msft").get_json()
    assert len(only) == 1 and only[0]["price_at_discussion"] == 100.0
    page = logged_in.get(f"/clients/{cid}")
    assert b"since discussion" in page.data
    csv = logged_in.get("/snapshots/export.csv?ticker=AAPL").data.decode()
    assert "AAPL" in csv and "MSFT" not in csv


def test_rm_cannot_see_other_rms_records(logged_in, client, app):
    logged_in.post("/snapshots/new", data={"client_id": "__new__", "new_client_code": "C-1",
                                            "new_client_name": "Private", "ticker": "AAPL", "price": "5"})
    with app.app_context():
        sid = Snapshot.query.one().id
        cid = Client.query.one().id
    login_as(client, "rm2")
    assert client.get("/api/snapshots").get_json() == []
    assert client.get(f"/clients/{cid}").status_code == 404
    assert client.get(f"/snapshots/{sid}/edit").status_code == 404
    assert client.post(f"/snapshots/{sid}/delete").status_code == 404


def test_invalid_snapshot_input(logged_in):
    r = logged_in.post("/snapshots/new", data={"client_id": "__new__", "new_client_code": "X",
                                                "new_client_name": "Y", "ticker": "AAPL", "price": "-3"})
    assert b"positive number" in r.data


def test_edit_and_delete_snapshot(logged_in, app):
    logged_in.post("/snapshots/new", data={"client_id": "__new__", "new_client_code": "C-1",
                                            "new_client_name": "Z", "ticker": "KO", "price": "60"})
    with app.app_context():
        s = Snapshot.query.one()
        sid, cid = s.id, s.client_id
    logged_in.post(f"/snapshots/{sid}/edit", data={"client_id": str(cid), "ticker": "KO", "price": "61.5",
                                                   "notes": "updated"})
    with app.app_context():
        assert db.session.get(Snapshot, sid).notes == "updated"
    logged_in.post(f"/snapshots/{sid}/delete")
    with app.app_context():
        assert Snapshot.query.count() == 0


def test_all_pages_render(logged_in):
    for url in ["/", "/compare", "/clients", "/clients/new", "/snapshots", "/snapshots/new"]:
        assert logged_in.get(url).status_code == 200, url
