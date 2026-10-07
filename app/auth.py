"""Login / logout for relationship managers."""
from functools import wraps

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

from .models import User, db

bp = Blueprint("auth", __name__)


@bp.before_app_request
def load_user():
    uid = session.get("user_id")
    g.user = db.session.get(User, uid) if uid else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not logged in"}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(request.form.get("password", "")):
            session.clear()
            session["user_id"] = user.id
            nxt = request.args.get("next", "")
            # Only allow local redirects.
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        if not username or not full_name or len(password) < 8:
            flash("Fill in every field. Passwords need at least 8 characters.", "error")
        elif User.query.filter_by(username=username).first():
            flash("That username is taken.", "error")
        else:
            user = User(username=username, full_name=full_name)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            flash(f"Welcome, {full_name}.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("register.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
