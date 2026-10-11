"""HACKNETIX Flask application.

Run:   flask --app app run --debug        (or: python app.py)
"""
import hmac
import logging
import os
import secrets

from flask import Flask, abort, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

import similarity
from config import Config
from extensions import db, login_manager


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    db.init_app(app)
    login_manager.init_app(app)

    import db_models  # noqa: F401  (register tables)
    with app.app_context():
        db.create_all()
        # Load the configured local fine-tuned model once at start-up.
        similarity.init_embedder(app.config["MODEL_ROOT"], app.config["MODEL_ZIP"],
                                 app.config["ALLOW_FALLBACK_EMBEDDER"])

    from routes.api import bp as api_bp
    from routes.pages_auth import auth, pages
    from routes.staff import bp as staff_bp
    for bp in (pages, auth, api_bp, staff_bp):
        app.register_blueprint(bp)

    import cli
    cli.register(app)

    # ---------------- auth plumbing
    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(db_models.User, int(uid))

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return jsonify(error="Please sign in."), 401
        from flask import redirect, url_for
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    # ---------------- CSRF (session token, sent as X-CSRF-Token header or csrf_token form field)
    @app.context_processor
    def inject_csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf_token": lambda: session["csrf"]}

    @app.before_request
    def csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            expected = session.get("csrf", "")
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
            if not expected or not hmac.compare_digest(expected, sent):
                abort(400, "Your session expired or the security token is missing. Refresh the page and try again.")

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---------------- errors: JSON for /api, friendly page elsewhere
    @app.errorhandler(Exception)
    def on_error(e):
        if isinstance(e, HTTPException):
            code, name, msg = e.code, e.name, e.description
        else:
            app.logger.exception("Unhandled error")
            code, name, msg = 500, "Server error", "Something went wrong on our side."
        if request.path.startswith("/api/"):
            return jsonify(error=msg), code
        return render_template("error.html", code=code, title=name, message=msg), code

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", "5000")))
