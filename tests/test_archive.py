import io
import json
from datetime import date
from decimal import Decimal

import pytest

from app.archive import archive_snapshots
from app.models import Client, Snapshot, db


class MemoryS3:
    def __init__(self, alter_payload=False):
        self.objects = {}
        self.alter_payload = alter_payload

    def put_object(self, Bucket, Key, Body, **_kwargs):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        body = self.objects[(Bucket, Key)]
        if self.alter_payload:
            body += b"corrupt"
        return {"Body": io.BytesIO(body)}


def _add_snapshot(discussed_on):
    client = Client(rm_id=1, client_code=f"C-{discussed_on.year}", name="Archive Test")
    db.session.add(client)
    db.session.flush()
    snapshot = Snapshot(
        rm_id=1,
        client_id=client.id,
        ticker="AAPL",
        company_name="Apple",
        price_at_discussion=Decimal("123.45"),
        discussed_on=discussed_on,
        interest_level="Medium",
        tags="long term",
        notes="Discussion notes",
    )
    db.session.add(snapshot)
    db.session.commit()
    return snapshot.id


def test_archive_only_snapshots_older_than_cutoff(app):
    with app.app_context():
        old_id = _add_snapshot(date(2019, 12, 31))
        cutoff_id = _add_snapshot(date(2020, 1, 1))
        new_id = _add_snapshot(date(2021, 5, 1))
        s3 = MemoryS3()

        count = archive_snapshots("archive-bucket", s3, years=5, today=date(2025, 1, 1))

        assert count == 1
        assert db.session.get(Snapshot, old_id) is None
        assert db.session.get(Snapshot, cutoff_id) is not None
        assert db.session.get(Snapshot, new_id) is not None
        assert len(s3.objects) == 1
        archived = json.loads(next(iter(s3.objects.values())))
        assert archived["notes"] == "Discussion notes"
        assert archived["client_name"] == "Archive Test"
        assert archived["price_at_discussion"] == "123.4500"


def test_archive_verification_failure_keeps_database_row(app):
    with app.app_context():
        snapshot_id = _add_snapshot(date(2019, 12, 31))

        with pytest.raises(RuntimeError, match="S3 archive verification failed"):
            archive_snapshots(
                "archive-bucket",
                MemoryS3(alter_payload=True),
                years=5,
                today=date(2025, 1, 1),
            )

        assert db.session.get(Snapshot, snapshot_id) is not None
