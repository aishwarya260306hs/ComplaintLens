"""Staff-only JSON API. Every query is scoped to the staff member's organization (admins see all)."""
from collections import defaultdict

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user
from sqlalchemy import func, or_

import services
from db_models import (OPEN_STATUSES, PRIORITIES, PRIORITY_RANK, STAFF_STATUSES, STATUSES, Announcement, Complaint,
                       ComplaintGroup, GroupNote, Organization, Suggestion, db, utcnow)
from organization_data import SUPPORTED_SECTORS
from utils import api_error, in_scope, iso, staff_org_scope, staff_required

bp = Blueprint("staff_api", __name__, url_prefix="/api")


def _group_or_404(gid):
    g = db.session.get(ComplaintGroup, gid)
    if not g or not in_scope(g.organization_id):
        abort(404)
    return g


def _suggestion_or_404(sid):
    s = db.session.get(Suggestion, sid)
    if not s or not in_scope(s.complaint.organization_id):
        abort(404)
    return s


def _dashboard_queries():
    org_value = request.args.get("org", "").strip()
    if current_user.role == "admin" and org_value:
        try:
            requested_org = int(org_value)
        except ValueError:
            return None, None, None, None, api_error("Choose a valid organization.", 422)
        if requested_org <= 0 or not db.session.get(Organization, requested_org):
            return None, None, None, None, api_error("Choose a valid organization.", 422)
        scope = requested_org
    else:
        scope = staff_org_scope()

    status = request.args.get("status", "").strip()
    if status and status not in STATUSES:
        return None, None, None, None, api_error("Choose a valid complaint status.", 422)
    sector = request.args.get("sector", "").strip().lower()
    if sector and sector not in SUPPORTED_SECTORS:
        return None, None, None, None, api_error("Choose a valid sector.", 422)
    location = request.args.get("location", "").strip()

    cq = Complaint.query
    gq = ComplaintGroup.query
    if scope is not None:
        cq = cq.filter(Complaint.organization_id == scope)
        gq = gq.filter(ComplaintGroup.organization_id == scope)
    if sector:
        org_ids = Organization.query.with_entities(Organization.id).filter(
            func.lower(Organization.sector) == sector
        )
        cq = cq.filter(Complaint.organization_id.in_(org_ids))
        gq = gq.filter(ComplaintGroup.organization_id.in_(org_ids))
    if location:
        cq = cq.filter(Complaint.location.ilike(f"%{location}%"))
        gq = gq.filter(ComplaintGroup.id.in_(cq.with_entities(Complaint.group_id).distinct()))
    if status:
        cq = cq.filter(Complaint.status == status)
        gq = gq.filter(ComplaintGroup.status == status)
    return cq, gq, scope, location, None


# ------------------------------------------------------------ summary + lists
@bp.get("/staff/summary")
@staff_required
def summary():
    cq, gq, scope, _location, error = _dashboard_queries()
    if error:
        return error
    complaint_ids = cq.with_entities(Complaint.id)
    group_ids = cq.with_entities(Complaint.group_id).distinct()
    multi_complaint_groups = (cq.with_entities(Complaint.group_id)
                              .group_by(Complaint.group_id)
                              .having(func.count(Complaint.id) > 1))
    sq = db.session.query(func.count(Suggestion.id)).join(Complaint, Complaint.id == Suggestion.complaint_id)\
        .filter(Suggestion.status == "pending", Complaint.id.in_(complaint_ids))
    org = db.session.get(Organization, scope) if scope and scope > 0 else None
    return jsonify(
        organization=org.name if org else "All organizations",
        total_complaints=cq.count(),
        open_complaints=cq.filter(Complaint.status != "Resolved").count(),
        resolved_complaints=cq.filter(Complaint.status == "Resolved").count(),
        similar_groups=gq.filter(ComplaintGroup.id.in_(multi_complaint_groups)).count(),
        total_groups=gq.filter(ComplaintGroup.id.in_(group_ids)).count(),
        open=gq.filter(ComplaintGroup.status.in_(OPEN_STATUSES)).count(),
        in_progress=gq.filter(ComplaintGroup.status == "In Progress").count(),
        resolved=gq.filter(ComplaintGroup.status == "Resolved").count(),
        high_priority=gq.filter(ComplaintGroup.priority == "high", ComplaintGroup.status != "Resolved").count(),
        pending_suggestions=sq.scalar() or 0,
        evaluation=dict(available=False, message="Evaluation data unavailable."),
    )


@bp.get("/staff/groups")
@staff_required
def groups():
    cq, gq, _scope, _location, error = _dashboard_queries()
    if error:
        return error
    q = (db.session.query(ComplaintGroup, func.count(Complaint.id))
         .join(Complaint, Complaint.group_id == ComplaintGroup.id).group_by(ComplaintGroup.id))
    q = q.filter(ComplaintGroup.id.in_(gq.with_entities(ComplaintGroup.id)))
    q = q.filter(Complaint.id.in_(cq.with_entities(Complaint.id)))
    priority = request.args.get("priority", "").lower()
    if priority in PRIORITIES:
        q = q.filter(ComplaintGroup.priority == priority)
    term = request.args.get("q", "").strip().lstrip("#")
    if term:
        q = q.filter(or_(ComplaintGroup.title.ilike(f"%{term}%"), Complaint.title.ilike(f"%{term}%")))
    rows = q.all()

    ids = [g.id for g, _ in rows]
    pending = dict(db.session.query(Suggestion.group_id, func.count(Suggestion.id))
                   .filter(Suggestion.status == "pending", Suggestion.group_id.in_(ids))
                   .group_by(Suggestion.group_id).all()) if ids else {}
    locs = defaultdict(set)
    if ids:
        for gid, loc in db.session.query(Complaint.group_id, Complaint.location).filter(Complaint.group_id.in_(ids)):
            locs[gid].add(loc)

    sort = request.args.get("sort", "priority")
    keyfn = {
        "count": lambda r: (-r[1], -r[0].updated_at.timestamp()),
        "updated": lambda r: -r[0].updated_at.timestamp(),
    }.get(sort, lambda r: (-PRIORITY_RANK.get(r[0].priority, 1), -r[1], -r[0].updated_at.timestamp()))
    rows.sort(key=keyfn)

    def loc(gid):
        s = locs[gid]
        return next(iter(s)) if len(s) == 1 else f"{len(s)} locations"

    return jsonify(items=[dict(id=g.id, title=g.title, count=n, location=loc(g.id), priority=g.priority,
                               status=g.status, updated_at=iso(g.updated_at), pending_suggestions=pending.get(g.id, 0))
                          for g, n in rows[:300]])


@bp.get("/staff/suggestions")
@staff_required
def suggestions():
    cq, _gq, _scope, _location, error = _dashboard_queries()
    if error:
        return error
    q = (Suggestion.query.join(Complaint, Complaint.id == Suggestion.complaint_id)
         .filter(Suggestion.status == "pending", Complaint.id.in_(cq.with_entities(Complaint.id))))
    items = q.order_by(Suggestion.score.desc()).limit(200).all()
    return jsonify(items=[dict(
        id=s.id,
        score=s.score,
        score_scope="maximum similarity to a complaint in the suggested group",
        complaint=dict(id=s.complaint.id, title=s.complaint.title, status=s.complaint.status),
        group=dict(id=s.group.id, title=s.group.title, status=s.group.status),
        complaint_pairs=services.pairwise_similarity(s),
        location=s.complaint.location,
    ) for s in items])


@bp.post("/staff/suggestions/<int:sid>/<action>")
@staff_required
def review_suggestion(sid, action):
    if action not in ("confirm", "reject"):
        abort(404)
    s = _suggestion_or_404(sid)
    if s.status != "pending":
        return api_error("This suggestion has already been reviewed.", 409)
    (services.confirm_suggestion if action == "confirm" else services.reject_suggestion)(s, current_user)
    db.session.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------ group detail
@bp.get("/groups/<int:gid>")
@staff_required
def group_detail(gid):
    g = _group_or_404(gid)
    members = Complaint.query.filter_by(group_id=g.id).order_by(Complaint.created_at).all()
    proposed = (Suggestion.query.filter_by(group_id=g.id, status="pending")
                .order_by(Suggestion.score.desc()).all())
    return jsonify(
        id=g.id, title=g.title, summary=g.summary, priority=g.priority, status=g.status,
        priority_locked=bool(g.priority_locked),
        organization=g.organization_id and db.session.get(Organization, g.organization_id).name,
        location=services.group_location(g.id),
        confirmed=[dict(id=c.id, title=c.title, created_at=iso(c.created_at)) for c in members],
        proposed=[dict(suggestion_id=s.id, complaint_id=s.complaint_id, title=s.complaint.title,
                       status=s.complaint.status, score=s.score,
                       score_scope="maximum similarity to a complaint in the suggested group",
                       complaint_pairs=services.pairwise_similarity(s))
                  for s in proposed],
        history=[dict(action=e.action, by=e.by_name, at=iso(e.at), detail=e.detail) for e in g.events],
        notes=[dict(author=n.author_name, text=n.text, at=iso(n.created_at)) for n in g.notes if n.is_internal],
        updates=[dict(author=n.author_name, text=n.text, at=iso(n.created_at)) for n in g.notes if not n.is_internal],
    )


@bp.patch("/groups/<int:gid>")
@staff_required
def update_group(gid):
    g = _group_or_404(gid)
    d = request.get_json(silent=True) or {}

    status, priority = d.get("status"), d.get("priority")
    if status is not None and status not in STAFF_STATUSES:
        return api_error(f"Status must be one of: {', '.join(STAFF_STATUSES)}.", 422)
    if priority is not None and priority not in PRIORITIES:
        return api_error("Priority must be low, medium or high.", 422)

    if isinstance(d.get("title"), str) and d["title"].strip():
        g.title = d["title"].strip()[:200]
    if isinstance(d.get("summary"), str):
        g.summary = d["summary"].strip()[:1000]

    if priority and priority != g.priority:
        old = g.priority
        g.priority, g.priority_locked = priority, True
        services.log_event(g, f"Priority changed to {priority}", current_user.name, f"from {old} (set by staff)")
        for m in Complaint.query.filter_by(group_id=g.id):
            m.priority = priority
    if status:
        services.apply_group_status(g, status, current_user)

    note = str(d.get("internal_note") or "").strip()
    if note:
        db.session.add(GroupNote(group=g, author_id=current_user.id, author_name=current_user.name,
                                 text=note[:2000], is_internal=True))
        services.log_event(g, "Internal note added", current_user.name)
    public = str(d.get("public_update") or "").strip()
    if public:
        db.session.add(GroupNote(group=g, author_id=current_user.id, author_name=current_user.name,
                                 text=public[:2000], is_internal=False))
        services.log_event(g, "Update posted to complainants", current_user.name)
        for m in Complaint.query.filter_by(group_id=g.id):
            services.notify(m.user_id, f"New update on complaint #{m.id}.", f"/complaints/{m.id}")
    g.updated_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------ announcements
@bp.post("/staff/announcements")
@staff_required
def create_announcement():
    d = request.get_json(silent=True) or {}
    title, body = str(d.get("title") or "").strip(), str(d.get("body") or "").strip()
    if not title:
        return api_error("Title is required.", 422)
    org_id = current_user.organization_id if current_user.role == "staff" else d.get("organization_id")
    if not org_id or not db.session.get(Organization, org_id):
        return api_error("An organization is required.", 422)
    db.session.add(Announcement(organization_id=org_id, title=title[:160], body=body[:2000]))
    db.session.commit()
    return jsonify(ok=True), 201
