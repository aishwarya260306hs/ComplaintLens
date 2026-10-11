from functools import wraps

from flask import abort, jsonify, request
from flask_login import current_user, login_required


def iso(dt):
    """Naive-UTC datetime -> ISO string with Z so the browser parses it correctly."""
    return dt.isoformat() + "Z" if dt else None


def api_error(message, status=400, **extra):
    return jsonify(error=message, **extra), status


def is_staff():
    return current_user.is_authenticated and current_user.role in ("staff", "admin")


def staff_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not is_staff():
            if request.path.startswith("/api/"):
                return api_error("Staff access required.", 403)
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def staff_org_scope():
    """Org id staff may see. Admin: optional ?org= filter (None = every organization)."""
    if current_user.role == "admin":
        return request.args.get("org", type=int)
    return current_user.organization_id or -1          # -1 matches nothing


def in_scope(org_id):
    if current_user.role == "admin":
        return True
    return current_user.organization_id is not None and current_user.organization_id == org_id
