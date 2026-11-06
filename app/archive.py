"""Archive old records to private S3 storage.

Saved discussions (the main record) and stock notes whose discussion date is
more than `years` old are written to S3, read back and compared byte for byte,
and only then deleted from RDS. Keys sit under snapshots/, so the bucket's
lifecycle rule moves them to Glacier after 30 days.
"""
import hashlib
import json

from .models import Discussion, Snapshot, db
from .timeutil import local_today

ARCHIVE_LOCK = "nimbus-archive"


def _acquire_lock(name):
    """MySQL named lock, so only one server runs the archive at a time.

    Every server in the Auto Scaling group has the monthly timer; the first to get
    the lock runs the job and the others skip it. SQLite (local) has one process,
    so no lock is needed there.
    """
    if db.engine.dialect.name != "mysql":
        return True
    got = db.session.execute(db.text("SELECT GET_LOCK(:name, 0)"), {"name": name}).scalar()
    return got == 1


def _release_lock(name):
    if db.engine.dialect.name == "mysql":
        db.session.execute(db.text("SELECT RELEASE_LOCK(:name)"), {"name": name})


def run_exclusively(name, job):
    """Run job() only if this server gets the lock; return its result, or None if skipped."""
    if not _acquire_lock(name):
        return None
    try:
        return job()
    finally:
        _release_lock(name)


def _snapshot_payload(snapshot):
    return {
        "id": snapshot.id,
        "rm_id": snapshot.rm_id,
        "client_id": snapshot.client_id,
        "client_code": snapshot.client.client_code,
        "client_name": snapshot.client.name,
        "ticker": snapshot.ticker,
        "company_name": snapshot.company_name,
        "price_at_discussion": str(snapshot.price_at_discussion),
        "currency": snapshot.currency,
        "discussed_on": snapshot.discussed_on.isoformat(),
        "interest_level": snapshot.interest_level,
        "tags": snapshot.tags,
        "notes": snapshot.notes,
        "follow_up_on": snapshot.follow_up_on.isoformat() if snapshot.follow_up_on else None,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


def _archive_snapshot(snapshot, bucket, s3_client):
    payload = json.dumps(_snapshot_payload(snapshot), sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    key = f"snapshots/{snapshot.id}/{digest}.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="application/json",
        Metadata={"sha256": digest},
    )
    stored = s3_client.get_object(Bucket=bucket, Key=key)
    archived_payload = stored["Body"].read()
    if archived_payload != payload or hashlib.sha256(archived_payload).hexdigest() != digest:
        raise RuntimeError(f"S3 archive verification failed for snapshot {snapshot.id}.")

    db.session.delete(snapshot)
    db.session.commit()


def archive_snapshots(bucket, s3_client, years=5, today=None):
    """Archive snapshots discussed before the calendar-date cutoff; return count."""
    if years < 1:
        raise ValueError("Archive age must be at least one year.")
    today = today or local_today()
    try:
        cutoff = today.replace(year=today.year - years)
    except ValueError:
        cutoff = today.replace(year=today.year - years, day=28)

    archived_count = 0
    while True:
        snapshots = (
            Snapshot.query.filter(Snapshot.discussed_on < cutoff)
            .order_by(Snapshot.id)
            .limit(100)
            .all()
        )
        if not snapshots:
            return archived_count
        for snapshot in snapshots:
            _archive_snapshot(snapshot, bucket, s3_client)
            archived_count += 1



def _cutoff(today, years):
    if years < 1:
        raise ValueError("Archive age must be at least one year.")
    today = today or local_today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February
        return today.replace(year=today.year - years, day=28)


def _discussion_payload(d):
    return {
        "id": d.id,
        "rm_id": d.rm_id,
        "client_id": d.client_id,
        "client_code": d.client.client_code,
        "client_name": d.client.name,
        "discussed_on": d.discussed_on.isoformat(),
        "period": d.period,
        "client_interests": d.client_interests,
        "notes": d.notes,
        "follow_up_on": d.follow_up_on.isoformat() if d.follow_up_on else None,
        "data_source": d.data_source,
        "data_retrieved_at": d.data_retrieved_at.isoformat() if d.data_retrieved_at else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "companies": [{
            "ticker": c.ticker, "company_name": c.company_name, "currency": c.currency,
            "stock_price": None if c.stock_price is None else str(c.stock_price),
            "price_date": c.price_date.isoformat() if c.price_date else None,
            "price_source": c.price_source, "fiscal_year": c.fiscal_year,
            "revenue_growth": c.revenue_growth, "net_margin": c.net_margin, "debt_to_equity": c.debt_to_equity,
        } for c in d.companies],
        "snapshot_json": d.snapshot_json,  # the full comparison as shown
    }


def _archive_discussion(d, bucket, s3_client):
    payload = json.dumps(_discussion_payload(d), sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    key = f"snapshots/discussions/{d.id}/{digest}.json"
    s3_client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/json",
                         Metadata={"sha256": digest})
    stored = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if stored != payload or hashlib.sha256(stored).hexdigest() != digest:
        raise RuntimeError(f"S3 archive verification failed for discussion {d.id}.")
    db.session.delete(d)  # its companies go with it
    db.session.commit()


def archive_discussions(bucket, s3_client, years=5, today=None):
    """Archive saved discussions dated before the cutoff; return the count."""
    cutoff = _cutoff(today, years)
    count = 0
    while True:
        batch = (Discussion.query.filter(Discussion.discussed_on < cutoff)
                 .order_by(Discussion.id).limit(100).all())
        if not batch:
            return count
        for d in batch:
            _archive_discussion(d, bucket, s3_client)
            count += 1
