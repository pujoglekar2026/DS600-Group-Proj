"""Archive old discussion snapshots to private S3 storage."""
import hashlib
import json
from datetime import date

from .models import Snapshot, db


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
    today = today or date.today()
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
