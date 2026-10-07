"""Command-line helpers:  flask --app wsgi <command>"""
import random
from datetime import date, timedelta
from decimal import Decimal

import click

from .market import DemoProvider
from .models import Client, Snapshot, User, db


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create all tables (safe to run more than once)."""
        db.create_all()
        click.echo("Database tables are ready.")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.argument("full_name")
    @click.password_option()
    def create_user(username, full_name, password):
        """Create a relationship-manager login."""
        username = username.strip().lower()
        if User.query.filter_by(username=username).first():
            raise click.ClickException(f"User '{username}' already exists.")
        user = User(username=username, full_name=full_name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created user {username}.")

    @app.cli.command("seed-demo")
    @click.option("--username", default="demo", show_default=True)
    @click.option("--password", default="demo12345", show_default=True)
    def seed_demo(username, password):
        """Create a demo RM with sample clients and discussions (for presentations)."""
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(username=username, full_name="Demo Relationship Manager")
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
        demo = DemoProvider()
        rng = random.Random(7)
        people = [
            ("C-1001", "Tan Wei Ming", "Private Banking", "Balanced"),
            ("C-1002", "Priya Raman", "Private Banking", "Growth"),
            ("C-1003", "Harbourview Logistics Pte Ltd", "Corporate", "Conservative"),
            ("C-1004", "Lim Family Office", "Private Banking", "Conservative"),
        ]
        ideas = [
            ("D05.SI", "dividends, SG banks", "Wants steady income; asked how DBS compares with OCBC on yield."),
            ("AAPL", "tech, long term", "Interested in adding US tech exposure; concerned about valuation."),
            ("NVDA", "AI, growth", "Asked about AI-related names; comfortable with volatility."),
            ("JPM", "US banks", "Comparing US vs SG banks on ROE and P/B."),
            ("KO", "defensive, dividends", "Looking for defensive holdings ahead of a possible slowdown."),
            ("MSFT", "tech, cloud", "Wants to understand cloud revenue growth."),
        ]
        for code, name, seg, risk in people:
            client = Client.query.filter_by(rm_id=user.id, client_code=code).first()
            if client is None:
                client = Client(rm_id=user.id, client_code=code, name=name, segment=seg, risk_profile=risk)
                db.session.add(client)
                db.session.flush()
            for ticker, tags, note in rng.sample(ideas, 3):
                days_ago = rng.randint(5, 200)
                hist = demo.history(ticker, "1y")
                price = hist[max(len(hist) - 1 - int(days_ago * 5 / 7), 0)][1]
                db.session.add(Snapshot(
                    rm_id=user.id, client_id=client.id, ticker=ticker,
                    company_name=demo.info(ticker)["name"], currency=demo.info(ticker)["currency"],
                    price_at_discussion=Decimal(str(price)),
                    discussed_on=date.today() - timedelta(days=days_ago),
                    interest_level=rng.choice(Snapshot.INTEREST_LEVELS), tags=tags, notes=note,
                    follow_up_on=date.today() + timedelta(days=rng.randint(-5, 30)) if rng.random() < 0.6 else None,
                ))
        db.session.commit()
        click.echo(f"Demo data ready. Log in as '{username}' / '{password}'.")
