"""Business logic: creating complaints, AI analysis, suggestions, grouping, status changes."""
import logging

import numpy as np
from flask import current_app

import similarity
from db_models import (PRIORITIES, PRIORITY_RANK, AppMeta, Complaint, ComplaintGroup, GroupEvent,
                       Notification, StatusHistory, Suggestion, User, db, utcnow)

log = logging.getLogger("hacknetix.services")


# ------------------------------------------------------------------ small helpers
def snippet(text, n=240):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def complaint_text(c):
    return f"{c.title}. {c.description}"


def pairwise_similarity(suggestion):
    """Return organizer-only cosine scores for comparable complaints in its target group."""
    source = suggestion.complaint
    if not source.embedding or not source.embedding_model:
        return []
    source_vector = np.frombuffer(source.embedding, dtype=np.float32)
    candidates = Complaint.query.filter_by(
        group_id=suggestion.group_id,
        organization_id=source.organization_id,
    ).filter(
        Complaint.embedding.isnot(None),
        Complaint.embedding_model == source.embedding_model,
    ).order_by(Complaint.id).all()
    pairs = []
    for candidate in candidates:
        vector = np.frombuffer(candidate.embedding, dtype=np.float32)
        if vector.shape != source_vector.shape:
            raise ValueError(f"Embedding dimension mismatch for complaint #{candidate.id}.")
        denominator = float(np.linalg.norm(source_vector) * np.linalg.norm(vector))
        if denominator == 0:
            continue
        score = float(np.dot(source_vector, vector) / denominator)
        pairs.append(dict(
            complaint_id=candidate.id,
            title=candidate.title,
            status=candidate.status,
            score=round(min(1.0, max(0.0, score)), 4),
        ))
    return pairs


def notify(user_id, message, link=None):
    db.session.add(Notification(user_id=user_id, message=message[:300], link=link))


def log_event(group, action, by=None, detail=None):
    db.session.add(GroupEvent(group=group, action=action[:200], by_name=by or "System", detail=(detail or "")[:300]))


def meta_get(key, default=None):
    row = db.session.get(AppMeta, key)
    return row.value if row else default


def meta_set(key, value):
    row = db.session.get(AppMeta, key)
    if row:
        row.value = value
    else:
        db.session.add(AppMeta(key=key, value=value))


def group_location(group_id):
    locs = sorted({r[0] for r in db.session.query(Complaint.location).filter(Complaint.group_id == group_id)})
    if len(locs) == 1:
        return locs[0]
    return f"{len(locs)} locations" if locs else ""


def set_complaint_status(c, status):
    """Change status, record history, return True if it changed."""
    if c.status == status:
        return False
    c.status = status
    c.updated_at = utcnow()
    db.session.add(StatusHistory(complaint=c, status=status))
    return True


# ------------------------------------------------------------------ priority
def computed_priority(members):
    """Highest complainant severity; escalated when many people report the same thing."""
    rank = max((PRIORITY_RANK.get(m.severity, 1) for m in members), default=1)
    n = len(members)
    if n >= 5:
        rank = max(rank, 2)
    elif n >= 3:
        rank = max(rank, 1)
    return PRIORITIES[rank]


def recompute_group(group):
    members = Complaint.query.filter_by(group_id=group.id).all()
    if not group.priority_locked and members:
        group.priority = computed_priority(members)
    for m in members:
        m.priority = group.priority
    group.updated_at = utcnow()


# ------------------------------------------------------------------ AI analysis
def analyze(c):
    """Embed the complaint and create *pending* suggestions against other groups in the same
    organization. Only compares vectors from the same embedding model."""
    emb = similarity.get_embedder()
    vec = emb.encode([complaint_text(c)])[0]
    c.embedding = vec.astype(np.float32).tobytes()
    c.embedding_model = emb.name

    threshold = emb.threshold(current_app.config["SIMILARITY_THRESHOLD"])
    rows = (db.session.query(Complaint.group_id, Complaint.embedding)
            .filter(Complaint.organization_id == c.organization_id,
                    Complaint.id != c.id,
                    Complaint.group_id != c.group_id,
                    Complaint.embedding.isnot(None),
                    Complaint.embedding_model == emb.name)
            .all())

    best = {}
    if rows:
        matrix = np.vstack([np.frombuffer(r.embedding, dtype=np.float32) for r in rows])
        for r, s in zip(rows, matrix @ vec):
            best[r.group_id] = max(best.get(r.group_id, -1.0), float(s))

    ranked = sorted(((s, gid) for gid, s in best.items() if s >= threshold), reverse=True)
    ranked = ranked[: current_app.config["MAX_SUGGESTIONS"]]
    seen = {s.group_id for s in Suggestion.query.filter_by(complaint_id=c.id)}
    created = []
    for score, gid in ranked:
        if gid in seen:
            continue
        s = Suggestion(complaint_id=c.id, group_id=gid, score=round(min(1.0, max(0.0, score)), 4), status="pending")
        db.session.add(s)
        created.append(s)
    return created


def create_complaint(user, org, *, category, title, description, location, severity, image_filename=None):
    """Persist the complaint FIRST (so it is never lost), then run the AI analysis.
    Returns (complaint, analysis_ok)."""
    c = Complaint(organization_id=org.id, user_id=user.id, category=category, title=title,
                  description=description, location=location, severity=severity, priority=severity,
                  status="Submitted", image_filename=image_filename)
    group = ComplaintGroup(organization_id=org.id, title=title, summary=snippet(description),
                           priority=severity, status="Submitted")
    c.group = group
    db.session.add_all([c, group])
    db.session.flush()
    db.session.add(StatusHistory(complaint=c, status="Submitted"))
    log_event(group, "Group created", "System", f"First complaint: #{c.id}")
    set_complaint_status(c, "Under AI Analysis")
    group.status = "Under AI Analysis"
    db.session.commit()
    cid = c.id

    try:
        finish_analysis(c)
        db.session.commit()
        return c, True
    except Exception:
        db.session.rollback()
        log.exception("AI analysis failed for complaint %s; it stays in 'Under AI Analysis'", cid)
        return db.session.get(Complaint, cid), False


def finish_analysis(c):
    suggestions = analyze(c)
    set_complaint_status(c, "Awaiting Staff Review")
    c.group.status = "Awaiting Staff Review"
    c.group.updated_at = utcnow()
    n = len(suggestions)
    log_event(c.group, "AI analysis complete", "System",
              f"{n} similar group(s) suggested" if n else "No similar groups found")
    notify(c.user_id, f"Complaint #{c.id} received. AI found {n} possibly similar issue(s); staff will review.",
           f"/complaints/{c.id}/analysis")
    for staff in User.query.filter_by(organization_id=c.organization_id, role="staff"):
        notify(staff.id, f"New complaint #{c.id}: {c.title}" + (f" ({n} suggestion(s) to review)" if n else ""),
               f"/staff/groups/{c.group_id}")


# ------------------------------------------------------------------ staff actions
def confirm_suggestion(s, staff):
    c, target, old = s.complaint, s.group, s.complaint.group
    now = utcnow()
    s.status, s.reviewed_by_id, s.reviewed_at = "confirmed", staff.id, now

    if c.group_id != target.id:
        # the complaint's other open proposals are moot now
        for other in Suggestion.query.filter(Suggestion.complaint_id == c.id, Suggestion.id != s.id,
                                             Suggestion.status == "pending"):
            other.status = "superseded"
        c.group_id = target.id
        db.session.flush()
        log_event(target, f"Complaint #{c.id} added to group", staff.name,
                  f"Staff confirmed AI suggestion (similarity {s.score:.0%})")
        set_complaint_status(c, target.status)       # join the group's current status
        notify(c.user_id, f"Your complaint #{c.id} was grouped with similar reports. Status: {target.status}.",
               f"/complaints/{c.id}")
        if old is not None and old.id != target.id:
            _retire_group_if_empty(old, target)
    recompute_group(target)


def _retire_group_if_empty(old, target):
    if Complaint.query.filter_by(group_id=old.id).count() > 0:
        recompute_group(old)
        return
    # Other complaints that wanted to join `old` should now be proposed for `target`.
    for sug in Suggestion.query.filter_by(group_id=old.id, status="pending").all():
        dup = Suggestion.query.filter_by(complaint_id=sug.complaint_id, group_id=target.id).first()
        member = Complaint.query.filter_by(id=sug.complaint_id, group_id=target.id).first()
        if dup or member:
            db.session.delete(sug)
        else:
            sug.group_id = target.id
    db.session.flush()
    Suggestion.query.filter_by(group_id=old.id).delete()
    db.session.delete(old)


def reject_suggestion(s, staff):
    s.status, s.reviewed_by_id, s.reviewed_at = "rejected", staff.id, utcnow()
    log_event(s.group, f"Suggestion rejected: complaint #{s.complaint_id}", staff.name,
              f"Similarity {s.score:.0%}; kept separate")


def apply_group_status(group, status, actor):
    if group.status == status:
        return False
    old = group.status
    group.status = status
    group.updated_at = utcnow()
    log_event(group, f"Status changed to {status}", actor.name, f"from {old}")
    for m in Complaint.query.filter_by(group_id=group.id):
        if set_complaint_status(m, status):
            notify(m.user_id, f"Your complaint #{m.id} is now: {status}.", f"/complaints/{m.id}")
    return True
