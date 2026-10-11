"""HTML pages + sign-in / register / sign-out."""
import re
import secrets
import time
from urllib.parse import parse_qs, urlparse

from flask import Blueprint, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from db_models import Organization, User, db
from utils import staff_required

pages = Blueprint("pages", __name__)
auth = Blueprint("auth", __name__)


# ------------------------------------------------------------ pages (data comes from /api)
@pages.get("/")
def landing():
    return render_template("landing.html")


@pages.get("/organizations")
def organizations():
    return render_template("select_org.html")


@pages.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@pages.get("/complaints/new")
@login_required
def new_complaint():
    return render_template("raise_complaint.html")


@pages.get("/complaints/<int:cid>/analysis")
@login_required
def analysis(cid):
    return render_template("analysis.html")


@pages.get("/my-complaints")
@login_required
def my_complaints():
    return render_template("my_complaints.html")


@pages.get("/complaints/<int:cid>")
@login_required
def complaint_detail(cid):
    return render_template("complaint_detail.html")


@pages.get("/notifications")
@login_required
def notifications():
    return render_template("notifications.html")


@pages.get("/staff")
@staff_required
def staff():
    return render_template("staff_dashboard.html")


@pages.get("/staff/groups/<int:gid>")
@staff_required
def group_detail(gid):
    return render_template("group_detail.html")


# ------------------------------------------------------------ auth
_fails = {}          # simple in-memory brute-force guard: key -> [timestamps]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def safe_next(target):
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None


def _limited(key):
    now = time.time()
    recent = [t for t in _fails.get(key, []) if now - t < 600]
    _fails[key] = recent
    return len(recent) >= 5


def _start_session(user):
    session.clear()                      # new session id + new CSRF token (prevents fixation)
    session["csrf"] = secrets.token_hex(16)
    login_user(user)


def _home_for(user):
    return "/staff" if user.role in ("staff", "admin") else "/dashboard"


@auth.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"))
    if current_user.is_authenticated and request.method == "GET":
        return redirect(nxt or _home_for(current_user))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        key = f"{request.remote_addr}:{email}"
        user = User.query.filter_by(email=email).first()
        if _limited(key):
            error = "Too many attempts. Wait a few minutes and try again."
        elif user and user.check_password(request.form.get("password", "")):
            _fails.pop(key, None)
            _start_session(user)
            return redirect(nxt or _home_for(user))
        else:
            _fails.setdefault(key, []).append(time.time())
            error = "Incorrect email or password."
    return render_template("login.html", error=error, next=nxt or ""), (401 if error else 200)


@auth.route("/register", methods=["GET", "POST"])
def register():
    orgs = Organization.query.order_by(Organization.name).all()
    nxt = safe_next(request.values.get("next"))
    preselect = request.values.get("org", type=int)
    if not preselect and nxt:
        q = parse_qs(urlparse(nxt).query).get("org")
        preselect = int(q[0]) if q and q[0].isdigit() else None
    error, form = None, {}
    if request.method == "POST":
        form = request.form
        name = form.get("name", "").strip()
        email = form.get("email", "").strip().lower()
        pw = form.get("password", "")
        org = db.session.get(Organization, form.get("organization_id", type=int) or 0)
        if not 2 <= len(name) <= 120:
            error = "Please enter your name."
        elif not EMAIL_RE.match(email) or len(email) > 200:
            error = "Enter a valid email address."
        elif len(pw) < 8:
            error = "Password must be at least 8 characters."
        elif not org:
            error = "Choose your organization."
        elif User.query.filter_by(email=email).first():
            error = "An account with that email already exists."
        else:
            user = User(name=name, email=email, role="user", organization_id=org.id)   # staff are created via CLI only
            user.set_password(pw)
            db.session.add(user)
            db.session.commit()
            _start_session(user)
            return redirect(nxt or "/dashboard")
    selected = request.form.get("organization_id", type=int) if request.method == "POST" else preselect
    return render_template("register.html", error=error, orgs=orgs, form=form, selected=selected,
                           next=nxt or ""), (422 if error else 200)


@auth.get("/logout")
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("pages.landing"))
