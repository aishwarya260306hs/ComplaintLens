"""Public + signed-in user JSON API (the endpoints the frontend calls)."""
import os
import uuid
from datetime import timedelta

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

import services
import similarity
from db_models import (FAQ, OPEN_STATUSES, PRIORITIES, STATUSES, Announcement, Complaint, GroupNote,
                       Notification, Organization, Suggestion, db, utcnow)
from organization_data import SUPPORTED_SECTORS, dataset_index, load_dataset
from utils import api_error, in_scope, is_staff, iso

bp = Blueprint("api", __name__, url_prefix="/api")

IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
MAX_IMAGE = 5 * 1024 * 1024


# ============================================================ public
@bp.get("/health")
def health():
    e = similarity.get_embedder()
    return jsonify(status="ok", embedder=e.name,
                   threshold=e.threshold(current_app.config["SIMILARITY_THRESHOLD"]))


@bp.get("/stats")
def stats():
    q = db.session.query
    return jsonify(
        complaints=q(func.count(Complaint.id)).scalar() or 0,
        resolved=q(func.count(Complaint.id)).filter(Complaint.status == "Resolved").scalar() or 0,
        organizations=q(func.count(Organization.id)).scalar() or 0,
        is_sample=services.meta_get("sample_data") == "1",
    )


@bp.get("/organizations")
def organizations():
    try:
        dataset = load_dataset()
    except (OSError, ValueError) as exc:
        current_app.logger.exception("Organization dataset could not be loaded")
        return api_error(f"Organization directory is unavailable: {exc}", 503)
    metadata = dataset_index(dataset)
    state = request.args.get("state", "").strip().casefold()
    q = Organization.query
    sector = request.args.get("sector", "").strip().lower()
    if sector:
        q = q.filter(func.lower(Organization.sector) == sector)
    city = (request.args.get("city") or request.args.get("location") or "").strip()
    if city:
        q = q.filter(Organization.city.ilike(f"%{city}%"))
    term = request.args.get("q", "").strip()
    if term:
        q = q.filter(or_(Organization.name.ilike(f"%{term}%"), Organization.city.ilike(f"%{term}%")))
    requested_id = request.args.get("id", type=int)
    if requested_id:
        q = q.filter(Organization.id == requested_id)
    organizations = q.order_by(Organization.name).yield_per(500)
    items = []
    for org in organizations:
        entry = metadata.get((org.name.casefold(), (org.sector or "").casefold(), (org.city or "").casefold()))
        if state and (not entry or entry["state"].casefold() != state):
            continue
        items.append(dict(
            id=org.id, name=org.name, sector=org.sector, city=org.city,
            state=entry["state"] if entry else "",
            organization_type=entry["organization_type"] if entry else "",
            locations=org.locations,
            dataset_id=entry["dataset_id"] if entry else None,
            source_name=entry.get("source_name", "") if entry else "",
            source_url=entry.get("source_url", "") if entry else "",
            source_checked_on=entry.get("source_checked_on", "") if entry else "",
            dataset_name=dataset.get("dataset_name", ""),
            dataset_type=dataset.get("dataset_type", ""),
            dataset_completeness=dataset.get("completeness", ""),
            dataset_updated_on=dataset.get("updated_on", ""),
        ))
        if len(items) >= 100:
            break
    return jsonify(items=items, dataset=dict(
        name=dataset.get("dataset_name", ""),
        type=dataset.get("dataset_type", ""),
        completeness=dataset.get("completeness", ""),
        updated_on=dataset.get("updated_on", ""),
    ))


@bp.get("/categories")
def categories():
    return jsonify(items=[{"name": c} for c in current_app.config["CATEGORIES"]])


# ============================================================ helpers
def user_org():
    return db.session.get(Organization, current_user.organization_id) if current_user.organization_id else None


def visible_complaint_or_404(cid):
    c = db.session.get(Complaint, cid)
    # 404 (not 403) so IDs of other people's complaints are not revealed
    if not c or not (c.user_id == current_user.id or (is_staff() and in_scope(c.organization_id))):
        abort(404)
    return c


def complaint_card(c):
    return dict(id=c.id, title=c.title, category=c.category, location=c.location,
                organization=c.organization.name if c.organization else "",
                priority=c.priority, status=c.status,
                created_at=iso(c.created_at), updated_at=iso(c.updated_at))


def sniff_image(head):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


# ============================================================ dashboard
@bp.get("/dashboard")
@login_required
def dashboard():
    org = user_org()
    requested = request.args.get("org", type=int)
    if current_user.role == "admin":
        org = db.session.get(Organization, requested) if requested else Organization.query.order_by(Organization.id).first()
    elif requested and requested != current_user.organization_id:
        return api_error("Your account does not have access to that organization.", 403)
    if not org:
        return api_error("No organization is linked to this account.", 400)

    base = Complaint.query.filter_by(organization_id=org.id)
    count = lambda *cond: base.filter(*cond).count()
    mine = Complaint.query.filter_by(user_id=current_user.id, organization_id=org.id)
    recent = mine.order_by(Complaint.created_at.desc()).limit(5).all()

    common = []
    if is_staff():
        common = (db.session.query(Complaint.group_id, func.count(Complaint.id).label("n"))
                  .filter(Complaint.organization_id == org.id).group_by(Complaint.group_id)
                  .having(func.count(Complaint.id) > 1).order_by(func.count(Complaint.id).desc()).limit(5).all())
    from db_models import ComplaintGroup
    titles = {g.id: g.title for g in ComplaintGroup.query.filter(ComplaintGroup.id.in_([r[0] for r in common]))} if common else {}

    faqs = (FAQ.query.filter(or_(FAQ.organization_id == org.id, FAQ.organization_id.is_(None)))
            .order_by(FAQ.position, FAQ.id).limit(8).all())
    anns = Announcement.query.filter_by(organization_id=org.id).order_by(Announcement.created_at.desc()).limit(5).all()
    notes = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(5).all()

    return jsonify(
        organization=dict(id=org.id, name=org.name, location=org.city),
        user=dict(name=current_user.name),
        is_sample=services.meta_get("sample_data") == "1",
        counts=dict(mine=mine.count(),
                    open=count(Complaint.status.in_(OPEN_STATUSES)),
                    in_progress=count(Complaint.status == "In Progress"),
                    resolved=count(Complaint.status == "Resolved")),
        recent=[dict(id=c.id, title=c.title, status=c.status, priority=c.priority, created_at=iso(c.created_at)) for c in recent],
        common_issues=[dict(title=titles.get(gid, "Issue"), count=n) for gid, n in common],
        faqs=[dict(q=f.question, a=f.answer) for f in faqs],
        announcements=[dict(title=a.title, body=a.body, date=iso(a.created_at)) for a in anns],
        notifications=[dict(id=n.id, message=n.message, created_at=iso(n.created_at), read=n.is_read) for n in notes],
    )


# ============================================================ complaints
@bp.post("/complaints")
@login_required
def create_complaint():
    f = request.form
    user_type = f.get("user_type", "").strip().lower()
    sector = f.get("sector", "").strip().lower()
    category = f.get("category", "").strip()
    title = f.get("title", "").strip()
    description = f.get("description", "").strip()
    location = f.get("location", "").strip()
    severity = f.get("severity", "medium").strip().lower()

    errors = {}
    if current_user.role == "user" and user_type not in ("student", "employee"):
        errors["user_type"] = "Choose Student or Employee."
    elif user_type and user_type not in ("student", "employee"):
        errors["user_type"] = "Choose Student or Employee."
    if current_user.role == "user" and sector not in SUPPORTED_SECTORS:
        errors["sector"] = "Choose a supported sector."
    if not category or len(category) > 60:
        errors["category"] = "Choose a category."
    elif category not in current_app.config["CATEGORIES"]:
        errors["category"] = "Choose a valid complaint category."
    if not 8 <= len(title) <= 120:
        errors["title"] = "Title must be 8-120 characters."
    if not 30 <= len(description) <= 2000:
        errors["description"] = "Description must be 30-2000 characters."
    if not 1 <= len(location) <= 120:
        errors["location"] = "Enter a location."
    if severity not in PRIORITIES:
        errors["severity"] = "Severity must be low, medium or high."

    # Public complaints may be directed to any listed organization. Staff remain
    # limited to their assigned organization; admins retain their existing scope.
    requested_org = f.get("organization_id", type=int)
    if requested_org:
        org = db.session.get(Organization, requested_org)
        if current_user.role == "staff" and requested_org != current_user.organization_id:
            return api_error("Staff may submit only to their assigned organization.", 403)
        if not org:
            return api_error("Choose an organization from the directory.", 422)
        if sector and sector != (org.sector or "").casefold():
            return api_error("The organization does not belong to the selected sector.", 422)
        if current_user.role == "user":
            try:
                dataset = load_dataset()
            except (OSError, ValueError) as exc:
                current_app.logger.exception("Organization dataset could not be loaded")
                return api_error(f"Organization directory is unavailable: {exc}", 503)
            if not dataset_index(dataset).get((org.name.casefold(), (org.sector or "").casefold(), (org.city or "").casefold())):
                return api_error("Choose an organization from the current directory.", 422)
    elif current_user.role == "admin":
        org = None
    elif current_user.role == "user":
        org = None
        errors["organization"] = "Choose an organization from the directory."
    else:
        org = user_org()
    if not org:
        errors["organization"] = "Select an organization first."

    image_name = None
    file = request.files.get("image")
    if file and file.filename:
        data = file.read(MAX_IMAGE + 1)
        kind = sniff_image(data[:16])
        if len(data) > MAX_IMAGE or not kind:
            errors["image"] = "Use a PNG, JPG or WebP under 5 MB."
        elif not errors:
            image_name = f"{uuid.uuid4().hex}.{kind}"
            os.makedirs(current_app.config["UPLOAD_FOLDER"], exist_ok=True)
            with open(os.path.join(current_app.config["UPLOAD_FOLDER"], image_name), "wb") as fh:
                fh.write(data)

    if errors:
        return api_error(" ".join(errors.values()), 422, fields=errors)

    c, ok = services.create_complaint(current_user, org, category=category, title=title, description=description,
                                      location=location, severity=severity, image_filename=image_name)
    return jsonify(id=c.id, analysis_pending=not ok), 201


@bp.get("/complaints")
@login_required
def list_complaints():
    q = Complaint.query.options(joinedload(Complaint.organization))
    if request.args.get("mine") == "1" or current_user.role == "user":
        q = q.filter(Complaint.user_id == current_user.id)
    else:
        scope = (current_user.organization_id or -1) if current_user.role == "staff" else request.args.get("org", type=int)
        if scope is not None:
            q = q.filter(Complaint.organization_id == scope)

    status = request.args.get("status", "")
    if status in STATUSES:
        q = q.filter(Complaint.status == status)
    priority = request.args.get("priority", "").lower()
    if priority in PRIORITIES:
        q = q.filter(Complaint.priority == priority)
    category = request.args.get("category", "")
    if category:
        q = q.filter(Complaint.category == category)
    days = request.args.get("days", type=int)
    if days:
        q = q.filter(Complaint.created_at >= utcnow() - timedelta(days=days))
    term = request.args.get("q", "").strip().lstrip("#")
    if term:
        cond = Complaint.title.ilike(f"%{term}%")
        q = q.filter(or_(cond, Complaint.id == int(term)) if term.isdigit() else cond)

    items = q.order_by(Complaint.created_at.desc(), Complaint.id.desc()).limit(200).all()
    return jsonify(items=[complaint_card(c) for c in items])


@bp.get("/complaints/<int:cid>")
@login_required
def complaint_detail(cid):
    c = visible_complaint_or_404(cid)
    updates = []
    if c.group_id:
        updates = [dict(author="Staff", message=n.text, at=iso(n.created_at))
                   for n in GroupNote.query.filter_by(group_id=c.group_id, is_internal=False)
                   .order_by(GroupNote.created_at.desc(), GroupNote.id.desc())]
    return jsonify(**complaint_card(c), description=c.description, severity=c.severity,
                   image_url=f"/api/complaints/{c.id}/image" if c.image_filename else None,
                   history=[dict(status=h.status, at=iso(h.at)) for h in c.history],
                   updates=updates, feedback_submitted=c.rating is not None)


@bp.get("/complaints/<int:cid>/analysis")
@login_required
def complaint_analysis(cid):
    c = visible_complaint_or_404(cid)
    if not is_staff():
        return jsonify(id=c.id, status=c.status)
    sugs = (Suggestion.query.filter(Suggestion.complaint_id == c.id, Suggestion.status.in_(("pending", "confirmed")))
            .order_by(Suggestion.score.desc()).all())
    matches = []
    for s in sugs:
        m = dict(group_id=s.group_id, group_title=s.group.title, score=s.score,
                 score_scope="maximum similarity to a complaint in the suggested group",
                 complaint_pairs=services.pairwise_similarity(s), status=s.status,
                 location=services.group_location(s.group_id))
        m["title"] = s.group.title
        matches.append(m)
    return jsonify(id=c.id, status=c.status, priority=c.priority, matches=matches,
                   note="Similarity scores are not probabilities. Staff must confirm any grouping.")


@bp.get("/complaints/<int:cid>/image")
@login_required
def complaint_image(cid):
    c = visible_complaint_or_404(cid)
    if not c.image_filename:
        abort(404)
    ext = c.image_filename.rsplit(".", 1)[-1]
    resp = send_from_directory(current_app.config["UPLOAD_FOLDER"], c.image_filename,
                               mimetype=IMAGE_MIME.get(ext, "application/octet-stream"))
    resp.headers["Content-Disposition"] = "inline"
    return resp


@bp.post("/complaints/<int:cid>/feedback")
@login_required
def feedback(cid):
    c = visible_complaint_or_404(cid)
    if c.user_id != current_user.id:
        return api_error("Only the person who raised the complaint can leave feedback.", 403)
    if c.status != "Resolved":
        return api_error("Feedback opens once the complaint is resolved.", 409)
    if c.rating is not None:
        return api_error("Feedback was already submitted.", 409)
    data = request.get_json(silent=True) or {}
    rating = data.get("rating")
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        return api_error("Rating must be between 1 and 5.", 422)
    c.rating = rating
    c.feedback_comment = str(data.get("comment") or "")[:1000]
    c.feedback_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/complaints/<int:cid>/reopen")
@login_required
def reopen(cid):
    c = visible_complaint_or_404(cid)
    if c.user_id != current_user.id:
        return api_error("Only the person who raised the complaint can reopen it.", 403)
    if c.status != "Resolved":
        return api_error("Only resolved complaints can be reopened.", 409)
    services.set_complaint_status(c, "Awaiting Staff Review")
    g = c.group
    if g and g.status == "Resolved":
        g.status = "Awaiting Staff Review"
        g.updated_at = utcnow()
    if g:
        services.log_event(g, f"Complaint #{c.id} reopened", current_user.name, "Complainant said it was not resolved")
        from db_models import User
        for s in User.query.filter_by(organization_id=c.organization_id, role="staff"):
            services.notify(s.id, f"Complaint #{c.id} was reopened by the complainant.", f"/staff/groups/{g.id}")
    db.session.commit()
    return jsonify(ok=True)


# ============================================================ notifications
@bp.get("/notifications")
@login_required
def notifications():
    q = Notification.query.filter_by(user_id=current_user.id)
    unread = q.filter_by(is_read=False).count()
    if request.args.get("unread"):
        q = q.filter_by(is_read=False)
    items = q.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(50).all()
    return jsonify(unread_count=unread,
                   items=[dict(id=n.id, message=n.message, link=n.link, read=n.is_read, created_at=iso(n.created_at)) for n in items])


@bp.post("/notifications/read")
@login_required
def notifications_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify(ok=True)
