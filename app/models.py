"""Database tables, stored in Amazon RDS (MySQL) in production."""
from datetime import date, datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    """A relationship manager who logs in to the app."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    clients = db.relationship("Client", back_populates="rm", cascade="all, delete-orphan")
    snapshots = db.relationship("Snapshot", back_populates="rm", cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    """A client in a relationship manager's book."""

    __tablename__ = "clients"
    __table_args__ = (db.UniqueConstraint("rm_id", "client_code", name="uq_rm_client_code"),)

    id = db.Column(db.Integer, primary_key=True)
    rm_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    client_code = db.Column(db.String(32), nullable=False)  # e.g. C-1001
    name = db.Column(db.String(120), nullable=False)
    segment = db.Column(db.String(40))  # e.g. Private Banking, Corporate, Retail
    risk_profile = db.Column(db.String(20))  # Conservative / Balanced / Growth
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    rm = db.relationship("User", back_populates="clients")
    snapshots = db.relationship(
        "Snapshot", back_populates="client", cascade="all, delete-orphan",
        order_by="Snapshot.discussed_on.desc()",
    )

    @property
    def label(self) -> str:
        return f"{self.client_code} · {self.name}"


class Snapshot(db.Model):
    """A saved record of a client discussion about a stock."""

    __tablename__ = "snapshots"

    INTEREST_LEVELS = ("High", "Medium", "Low")

    id = db.Column(db.Integer, primary_key=True)
    rm_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    ticker = db.Column(db.String(16), nullable=False, index=True)
    company_name = db.Column(db.String(160))
    price_at_discussion = db.Column(db.Numeric(14, 4), nullable=False)
    currency = db.Column(db.String(8), default="USD")
    discussed_on = db.Column(db.Date, default=date.today, nullable=False, index=True)
    interest_level = db.Column(db.String(10), default="Medium")
    tags = db.Column(db.String(255))  # comma separated, e.g. "dividends, ESG"
    notes = db.Column(db.Text)
    follow_up_on = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    rm = db.relationship("User", back_populates="snapshots")
    client = db.relationship("Client", back_populates="snapshots")

    @property
    def tag_list(self):
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    def to_dict(self):
        return {
            "id": self.id,
            "client_code": self.client.client_code,
            "client_name": self.client.name,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "price_at_discussion": float(self.price_at_discussion),
            "currency": self.currency,
            "discussed_on": self.discussed_on.isoformat(),
            "interest_level": self.interest_level,
            "tags": self.tag_list,
            "notes": self.notes or "",
            "follow_up_on": self.follow_up_on.isoformat() if self.follow_up_on else None,
        }
