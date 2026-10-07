"""Nimbus RM - a stock comparison and client-notes app for relationship managers."""
import logging
import secrets

from flask import Flask, abort, render_template, request, session

from .config import Config
from .market import MarketData
from .models import db


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    logging.basicConfig(level=logging.INFO)

    db.init_app(app)
    app.extensions["market"] = MarketData(
        provider=app.config["MARKET_DATA_PROVIDER"],
        fallback=app.config["DEMO_FALLBACK"],
        ttl=app.config["CACHE_TTL"],
    )

    from .auth import bp as auth_bp
    from .routes import bp as main_bp
    from .api import bp as api_bp
    from .cli import register_cli

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    register_cli(app)

    # ---- CSRF protection for HTML forms (no extra dependency) -------------
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return session["_csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    # ---- Template formatting helpers --------------------------------------
    @app.template_filter("money")
    def money(value):
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return "—"

    @app.template_filter("pct")
    def pct(value, signed=False):
        try:
            return f"{float(value) * 100:{'+' if signed else ''}.1f}%"
        except (TypeError, ValueError):
            return "—"

    @app.before_request
    def check_csrf():
        if request.method == "POST" and not app.config.get("TESTING"):
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not sent or sent != session.get("_csrf"):
                abort(400, "Invalid or missing CSRF token. Reload the page and try again.")

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(400)
    def bad_request(err):
        return render_template("error.html", code=400, message=err.description), 400

    with app.app_context():
        try:
            db.create_all()
        except Exception as exc:  # e.g. two Gunicorn workers racing, or DB not up yet
            app.logger.warning("Could not create tables at startup: %s", exc)

    return app
